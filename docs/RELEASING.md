<!--
SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# Releasing Heartbreaker, and getting a key into an installed one

Every GitHub Release links here, because the published installer deliberately
ships **without** an API key and the download alone is not a working client.
This is the page that closes that gap.

## Why the installer has no key in it

This repository is public, so every Release asset is world-downloadable. The
`X-API-Key` is the entire authorization boundary in front of Igor — the owner's
memory, mail, calendar and portal sessions. Baking it into a published
installer hands all of that to anyone who clicks Download, and a string inside
an `asar` bundle is not hidden by having been compiled.

The **server address** is a different matter: a hostname behind auth is not a
secret. It is baked, from the repo variable `SPEDA_API_BASE`
(Settings → Secrets and variables → Actions → Variables). If that variable is
unset the build still succeeds but warns, and the installed app falls back to
`http://localhost:8000`.

## How the key reaches an installed app

`get-config` in [`src/main/index.ts`](../packages/heartbreaker/src/main/index.ts)
resolves the base and the key independently, each taking the first non-blank of:

1. **Runtime environment** — `SPEDA_API_BASE` / `SPEDA_API_KEY` in the
   environment the app is launched from.
2. **A build-time bake** — `MAIN_VITE_SPEDA_API_BASE` / `MAIN_VITE_SPEDA_API_KEY`,
   compiled in. CI sets only the first of these.
3. **`connection.json`** in Electron's `userData` directory, written by the
   connection popup.

With nothing found, it returns `http://localhost:8000` / `dev-key` and reports
`configured: false`.

For a **published release** the path is therefore always #3. On first launch
`configured: false` raises `ConnectionSetupModal`: enter the server URL and the
key, and it proves the address over the backend's unauthenticated `/health`
before saving. It takes effect immediately — no restart — and persists across
launches. To change it later: **Settings → Account → Edit**.

The key to enter is `SPEDA_API_KEY` from the server's
`packages/igor/.env`.

### The private alternative

[`build-app.ps1`](../build-app.ps1) builds locally **with** both values baked
(resolution step #2), which is appropriate precisely because the output is not
published:

```powershell
powershell -File build-app.ps1 -ApiBase https://speda.yourdomain.com -ApiKey <SPEDA_API_KEY>
```

Never hand an installer built this way to anyone: the key is in it.

## Versioning

**The tag is the source of truth.**
[`.github/workflows/heartbreaker.yml`](../.github/workflows/heartbreaker.yml)
reads the highest `vX.Y.Z` tag on each run and bumps the patch, so any push to
`main` touching `packages/heartbreaker/**` publishes the next patch release on
its own. `package.json`'s `version` is rewritten inside the runner and never
committed back — committing a bump from a push-triggered workflow either loops
or needs a token that can push to `main`, and the tag already says the same
thing unambiguously. Treat the number in `package.json` as a placeholder.

A one-off `minor` or `major` bump is available through **workflow_dispatch**,
which also picks which agent the build brands as and talks to.

### Opening a new series

`VERSION_FLOOR` in the Resolve version step is the lowest version the pipeline
will ever publish, and it is the lever for starting a new line. Raise it, and
the next run's computed patch falls under the floor and is clamped up to it:

| `VERSION_FLOOR` | highest tag | next release |
|---|---|---|
| `2.0.0` | `v2.0.49` | `v2.0.50` |
| `2.1.0` | `v2.0.49` | **`v2.1.0`** |
| `2.1.0` | `v2.1.0`  | `v2.1.1` |

No dispatch and no hand-pushed tag — the series moves with the commit that says
so, and the floor keeps documenting where the line started. It also still does
its original job of catching a hand-tagged `v0`/`v1`, which would otherwise ship
a release that installs over a newer one and reads as a downgrade.

## What a release run does

1. Resolves the version from tags (above).
2. Typechecks, then builds with `electron-vite` and packages with
   `electron-builder --win --publish never` — `never` because electron-builder's
   own GitHub publisher would race this workflow and open a second, draft
   release for the same tag.
3. Renames the asset to `SPEDA-Mark-VI-<version>-setup.exe`. Spaces in an asset
   name become dots in GitHub's download URL, and a predictable ASCII name is
   one less thing between the owner and the file.
4. Creates the tag and the Release, with notes listing the `packages/heartbreaker`
   commits since the previous tag.

Runs are serialized (`concurrency: heartbreaker-release`, never cancelled in
flight): two overlapping runs would read the same highest tag and claim the same
version, and a killed run can leave a tag pushed with no asset on it.

The build is **unsigned** — SmartScreen warns on first run. Expected for an
in-house build.
