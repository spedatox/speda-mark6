# Forge deployment assets

Forge is a native uv workspace package in the Speda Mark VI monorepo. Igor
imports `forge.runtime` directly; production does not clone, mount, synchronize,
start, or connect to a separate Forge checkout or service.

## Mark VI server

The Igor image installs `packages/forge` from the root workspace lock. Its only
Forge-specific runtime settings select the Cell backend, workspace boundary,
and Cell images. The workspace directory and Docker socket remain host-visible
because disposable Cells are launched by the host Docker daemon.

Build the Cell images from the monorepo root:

```bash
docker build -f packages/forge/deploy/cell-optimus.Dockerfile \
  -t forge-cell-optimus:latest packages/forge/deploy/
docker build -f packages/forge/deploy/cell-scourge.Dockerfile \
  -t forge-cell-scourge:latest packages/forge/deploy/
```

Normal server execution is entirely in-process:

```text
Mark VI -> Legion / ForgeExecutor -> IgorModelAdapter
        -> forge.runtime.execute() -> Warden -> Cell
```

No Forge URL, port, WebSocket, systemd unit, source mount, or source sync job is
part of this path.

## Standalone Forge

Install or run the same `packages/forge` distribution and use `forge chat`.
Standalone mode supplies its own local provider/model host and invokes the same
runtime, Warden, tools, and Cell implementation used by Mark VI.

`forge connect` and `forge serve` remain compatibility commands for existing
standalone installations. They are not Mark VI server deployment components.

## Provider bridge

`fcc-server.service` is retained for standalone installations that deliberately
use the local provider bridge. It is not required by Igor or `forge.runtime`.
