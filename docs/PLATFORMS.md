# Platform and accelerator support

Hotpath's orchestrator is CPU software: it can optimize any target whose correctness, benchmark,
and profile commands run on the host or in the configured container. The bundled PyTorch helpers
add portable accelerator discovery and synchronized measurement; they do not make an arbitrary
target portable by themselves.

Run this first:

```bash
hotpath doctor
hotpath doctor --require-gpu   # fail unless PyTorch sees a supported accelerator
```

`HOTPATH_TORCH_DEVICE=cuda|xpu|mps|cpu` forces the bundled target to use that backend and fails
clearly if it is unavailable. ROCm uses PyTorch's `cuda` device string, while reports label the
backend as `rocm`.

| Host / accelerator | Local execution | Docker execution | Kernel path |
| --- | --- | --- | --- |
| Windows CPU | Supported | Docker Desktop Linux containers | Python / native target code |
| Windows NVIDIA | PyTorch CUDA | `execution.gpu: device=0` with NVIDIA container support | CUDA or Triton with fallback |
| Linux NVIDIA | PyTorch CUDA | `execution.gpu: device=0` | CUDA or Triton with fallback |
| Linux AMD | PyTorch ROCm (`device=cuda`) | pass `/dev/kfd` and `/dev/dri` through `execution.devices` | ROCm-compatible PyTorch/Triton with fallback |
| Linux Intel | PyTorch XPU | pass `/dev/dri` through `execution.devices` | XPU-supported PyTorch with fallback |
| macOS Apple GPU | PyTorch MPS | Docker Desktop cannot expose Metal/MPS | MPS-supported PyTorch with CPU fallback |
| Any CPU-only host | Automatic CPU fallback | Supported | Portable CPU implementation |

ROCm Docker example:

```yaml
execution:
  backend: docker
  image: hotpath-runner:rocm-reviewed
  devices: [/dev/kfd, /dev/dri]
  group_add: [video, render]
```

The image must contain the vendor's matching PyTorch/runtime build. Accelerator drivers and
framework wheels are deliberately not installed by Hotpath: their correct versions depend on the
host driver, architecture, and security policy.

## Kernel-writing contract

- Include the call site and kernel file in `editable`; keep tests, benchmarks, references, and
  profilers locked.
- A hypothesis may change up to three files, enough for a kernel, dispatch wrapper, and call site.
- CUDA/Triton kernels must declare shape, stride, dtype, and device preconditions.
- Backend-specific code must dispatch explicitly and keep a known-correct PyTorch fallback.
- Every patch runs the locked correctness command before any benchmark.
- Fixed workload matrices reject an aggregate win if any declared shape regresses beyond its floor.
- A result is evidence only for the exact device, driver, framework, target commit, and workload
  recorded by that run. CI can prove control flow without pretending to own every GPU model.

## Certification boundary

Repository CI covers Windows, Ubuntu, and macOS host behavior; Linux CI additionally exercises the
real no-network/read-only Docker boundary. Physical CUDA, ROCm, XPU, and MPS performance runs remain
hardware certification jobs and must retain `hotpath doctor --json`, benchmark output, correctness
output, config, and target revision alongside the report.
