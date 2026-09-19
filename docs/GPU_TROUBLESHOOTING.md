# GPU troubleshooting (Windows laptop dGPU)

## First: is the GPU actually missing?
Run these. If both succeed, the GPU is fine and any note claiming otherwise is stale/other-machine:
```bash
nvidia-smi                                             # should list the GPU with a Bus-Id
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```
As of 2026-09-19 on the primary dev laptop both succeed (RTX 5060 Laptop GPU at bus `00000000:64:00.0`).

**Known misattribution on this laptop:** `Get-PnpDevice -Class Display` shows two
`USB3.0 5K Graphic Docking` adapters as `CM_PROB_PHANTOM` — that is an *unplugged USB display dock*,
not the GPU. The RTX 5060 shows `Status: OK / CM_PROB_NONE`. An earlier CLAUDE.md note that read the
phantom code as "the RTX 5060 is absent" was wrong; the GPU is fine.

## What `CM_PROB_PHANTOM` means
Windows Configuration Manager **problem code 45**: "Currently, this hardware device is not connected
to the computer." Windows has a registry node for the device but the hardware is **not enumerated on
the PCI bus right now**. On a laptop discrete GPU this is almost always a *power/enumeration* state,
not a dead card:
- **Hybrid graphics / Advanced Optimus**: the dGPU sits in D3cold (powered off) when idle and only
  appears under load or when a display/app requests it. A tool sampling at the wrong moment can see it
  as "phantom."
- **MUX / BIOS graphics mode** set to hybrid rather than discrete-only.
- **Driver/enumeration hiccup** after sleep/hibernate or a driver update.
- **A different machine entirely** (a CI box or cloud VM with no dGPU) — the most likely source of a
  committed "GPU absent" note.

It is **not** a CUDA or PyTorch install problem. If `torch` couldn't find CUDA you'd get a different
error; phantom is a Windows PnP/hardware-presence state.

## Diagnose the PnP state
PowerShell:
```powershell
Get-PnpDevice -Class Display | Format-Table FriendlyName, Status, Problem, InstanceId
```
- `Status: OK` → the GPU is present and usable (ignore any stale note).
- `Problem: CM_PROB_PHANTOM` (45) or `Status: Unknown` → it is not currently enumerated; try the fixes below.

## Fixes, cheapest first
1. **Wake the dGPU.** Run a CUDA workload / open an app that uses it, then re-check `nvidia-smi`. On
   Advanced Optimus the device often appears on demand.
2. **Reboot.** Clears most post-sleep/driver enumeration glitches.
3. **Force the dGPU on for dev work:** NVIDIA Control Panel → *Manage 3D settings* → *Preferred
   graphics processor* → *High-performance NVIDIA processor* (globally, or for `python.exe`).
4. **Set BIOS/UEFI graphics to Discrete/Dedicated** (disable hybrid/MUX) if the laptop exposes it —
   makes the dGPU always enumerated. (Costs battery; fine on a dev machine.)
5. **Reinstall/repair the driver** (clean install), then reboot. Driver here: 591.84 / CUDA 13.1.
6. **Rescan hardware:** Device Manager → *Action → Scan for hardware changes*, or
   `pnputil /scan-devices`.
7. Confirm it isn't disabled: Device Manager → Display adapters → right-click → *Enable device*.

## Running Hotpath on the GPU once it's present
```bash
python -m hotpath.cli run configs/torch_transformer.yaml --autocommit
```
`--autocommit` is required when the target's git tree has uncommitted changes — Hotpath measures
committed code only (the guard your teammate added). The offline mock run exercises the real CUDA
benchmark and should land around 1.5–2× (SDPA + sync-removal accepted; KV-prealloc correctly
rejected on this small model).
