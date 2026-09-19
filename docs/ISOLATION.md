# Execution isolation

Docker is the default backend. Missing Docker or a missing reviewed image fails closed. `execution.backend: local` explicitly opts into trusted host execution and inherits host credentials.

Build the starter CPU image from the Hotpath checkout, never from target code:

```sh
docker build -f docker/Dockerfile -t hotpath-runner:local .
```

The image supplies Python and Hotpath measurement helpers. Install other dependencies during an operator-reviewed image build, pin versions, and record the image digest. Never run target Dockerfiles or target dependency installers on the host. Preflight records the image ID; every stage must use that ID.

Each command receives a fresh container: read-only root and `/workspace`, no network, non-root UID/GID 65534, all capabilities dropped, no new privileges, CPU/memory/process quotas, bounded tmpfs, no persistent container logs, bounded captured output, and a timeout. Timeout, excessive output, cancellation, and completion force container removal. Temporary files and caches belong in `/tmp`; source writes fail. Candidate source and verification scripts are immutable at runtime.

Staging includes tracked regular files only, excluding Git metadata, `.ssh`, `.aws`, `.azure`, `.env*`, and private-key extensions. Symlinks, hardlinks, and escaping paths fail closed. The runner image cannot declare Docker `VOLUME` paths. Total staging is limited to 512 MiB. No host environment, home directory, Docker socket, or arbitrary host mounts enter containers. Filename filtering cannot detect other credential filenames or secrets embedded in ordinary source: targets must not themselves contain credentials.

Existing dirty repositories are rejected instead of silently committed. Commit or stash changes first; `hotpath run --autocommit` (or `HOTPATH_AUTOCOMMIT=1`) explicitly opts into snapshotting them into a commit. Fresh non-git targets are initialized and snapshotted. Workspace git operations disable hooks, fsmonitor, filters, external diff commands, and network protocols.

The dashboard and all API endpoints accept local clients only; the CLI refuses a
non-loopback bind. Run control cannot load an arbitrary config path from a request.
Start the server with the operator-selected config. For remote access, use an
authenticated reverse proxy that connects to the local listener.

## Dedicated Linux H100 runner

For hostile GPU code, run the orchestrator and Docker daemon in a dedicated disposable Linux GPU VM with no unrelated secrets, host data, or other tenants. Administratively install NVIDIA Container Toolkit and build a reviewed CUDA/PyTorch image:

```yaml
execution:
  backend: docker
  image: your-reviewed-image@sha256:YOUR_DIGEST
  gpu: device=0
  cpus: 4
  memory_mb: 16384
  pids_limit: 128
  tmpfs_mb: 1024
  output_limit_bytes: 1048576
search:
  max_parallel_benchmarks: 1
benchmark:
  exclusive: true
```

The disposable VM is part of the hostile-code deployment boundary: Docker shares the host kernel, and GPU access exposes driver interfaces. This implementation is not a microVM and cannot promise protection against kernel/driver vulnerabilities. An administratively installed runtime may be selected through `execution.runtime`; validate its GPU support separately. Keep the Docker daemon private. Image/runtime/GPU settings are operator-controlled, never supplied by an untrusted target.

## Verification and evidence

Unit tests cover construction of isolation arguments, staging, path escapes, output bounds and lifecycle cleanup. Set `HOTPATH_TEST_DOCKER=1` after building the image to run the real-container probe. It tests non-root execution, immutable source, missing host secret/socket, blocked external network and the process quota. Skips do not establish deployment safety; run the probe on the intended machine.

Isolation does not prove arbitrary tests are sufficient. Candidate code can influence tests in the same interpreter or emit misleading benchmark JSON. Operator-reviewed verification assets and an independent external evaluator are needed for adversarial measurement. Current results establish the configured test/benchmark contract, not mathematical equivalence or resistance to all benchmark manipulation.

Sources: Docker [container options](https://docs.docker.com/engine/containers/run/), [create reference](https://docs.docker.com/reference/cli/docker/container/create/), and [security model](https://docs.docker.com/engine/security/).
