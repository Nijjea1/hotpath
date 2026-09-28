# Security policy

Hotpath runs code it did not write: a target repository's tests, benchmarks, and profilers, and
patches that a model authored. Its security depends on where that code runs. Read
[`docs/ISOLATION.md`](docs/ISOLATION.md) before pointing Hotpath at a repository you do not trust.

## The short version

| Backend | Trust assumption | What it protects |
| --- | --- | --- |
| `execution.backend: docker` (default) | the target is untrusted | Each command runs in a fresh container: no network, read-only root and source, non-root UID, all capabilities dropped, CPU/memory/process quotas, no host environment, home directory, or Docker socket. Missing Docker or a missing reviewed image **fails closed**. |
| `execution.backend: local` | the target is **trusted** | Nothing. The target runs as you, with your environment and credentials. The bundled demo configs use it because the bundled targets are ours. |
| `hotpath go --sandbox auto` (default) | Docker when it is running; otherwise it asks before running the target on the host in a per-target virtualenv | A virtualenv is dependency isolation, **not** a security boundary. Use `--sandbox docker` for untrusted repositories so a missing Docker fails instead of falling back. |

Docker shares the host kernel, and GPU passthrough exposes driver interfaces. For hostile GPU code,
run the orchestrator in a disposable Linux VM with no unrelated secrets (see the H100 section of
`docs/ISOLATION.md`).

## What Hotpath does not claim

- **Correctness is relative to the locked test command.** Candidate code runs in the same
  interpreter as the tests and benchmark, so a sufficiently adversarial target can influence them.
  Locked files and a passing suite are evidence against the configured contract, not proof of
  semantic equivalence.
- **Secrets inside ordinary source files are not detected.** Staging drops `.env*`, `.ssh`, `.aws`,
  `.azure`, and private-key files, but a target must not contain credentials.
- **The dashboard is local-only by design.** Every API route rejects non-loopback clients and the
  CLI refuses a non-loopback bind. For remote access, put an authenticated reverse proxy in front.
- **Model output is never trusted.** Locked paths are enforced in code before a byte is written;
  model-authored text never chooses a command, a path outside `editable`, or a measurement.

## API keys

Keys are read from the environment, then a `.env` in the directory you run Hotpath from, then
`~/.hotpath/.env` (none overrides the real environment). They are never passed into Docker
containers. Do not run Hotpath from inside an untrusted checkout that ships its own `.env`. `hotpath doctor`
reports which keys are present without printing them; `hotpath doctor --verify-keys` asks each
endpoint whether the key still works.

## Reporting a vulnerability

Please **do not open a public issue** for a security problem. Report it privately through GitHub:
**https://github.com/Nijjea1/hotpath/security/advisories/new**.

Include the Hotpath version or commit, the execution backend, and the smallest reproduction you can.
Isolation escapes, locked-path bypasses, and ways for a target or model to influence an accept/reject
decision are all in scope. You should get an acknowledgement within a week.

## Supported versions

Hotpath is pre-1.0. Security fixes land on `main` and in the next release; there are no back-ports.
