# Releasing Heartbreaker

The desktop client ships from CI. Push to `main` touching
`packages/heartbreaker/**` and `.github/workflows/heartbreaker.yml` builds a
Windows installer, tags it, and publishes a GitHub Release.

```
push to main ──▶ resolve version from tags ──▶ npm ci ──▶ typecheck
                                                            │
                          electron-vite build ◀─────────────┘
                                    │
                          electron-builder --win
                                    │
                     upload artifact + gh release create
```

## Versioning

**The tag is the source of truth.** Every run reads the highest `vX.Y.Z` tag,
bumps the patch, and tags the result. With no tags at all it starts at
**2.0.0**.

| Situation | Next version |
|---|---|
| no tags yet | `2.0.0` |
| `v2.0.2`, push to main | `2.0.3` |
| `v2.0.2`, dispatch with `minor` | `2.1.0` |
| `v2.0.2`, dispatch with `major` | `3.0.0` |
| `v2.0.9` and `v2.0.10` exist | `2.0.11` — sorted numerically, not lexically |

That last row is the one that bites. `--sort=-v:refname` is git's version sort;
a plain `sort` puts `v2.0.9` above `v2.0.10` and the pipeline then re-releases
`2.0.10` forever, each run overwriting the last.

`package.json` is rewritten **in the runner and never committed back**. A
push-triggered workflow that commits either loops or needs a token that can
push to `main`, and neither earns its keep when a tag already says the same
thing. The consequence to know about: after a few releases the version in git
lags the latest tag. Local `npm run dist` builds therefore carry a stale number
— check the Releases page, not `package.json`, for what is actually shipping.

To raise a minor or major version, or to build a different agent's brand, use
**Actions → heartbreaker → Run workflow**.

## Configuration

| Kind | Name | Why |
|---|---|---|
| Variable | `SPEDA_API_BASE` | `https://your-server`. Baked in so the app knows where Igor is. |

Set it under **Settings → Secrets and variables → Actions → Variables**. Not a
secret: it is a hostname with authentication in front of it, and a variable
keeps it readable in the build log instead of masked to `***` when you are
trying to work out why a build points at the wrong server.

## The API key is not baked, and why

`build-app.ps1` bakes `MAIN_VITE_SPEDA_API_KEY` for local builds. **CI does
not**, because this repository is public and therefore so is every Release
asset.

Per CLAUDE.md Rule 12 the `X-API-Key` header is the *entire* authorization
boundary in front of Igor — memory, mail, calendar, health, portal sessions,
the lot. There is no public data endpoint and no second factor. A key baked
into a published installer is a key published to everyone who clicks Download,
and being compiled into an asar bundle does not hide a string; `npx asar
extract` is one command.

So the CI installer ships knowing *where* the server is and not *how to talk to
it*, and the key is supplied on the installed machine.

### Supplying the key

Until the app grows a setup screen, set it in the environment before launch:

```powershell
[Environment]::SetEnvironmentVariable('SPEDA_API_KEY', '<key>', 'User')
```

The main process resolves in this order (`packages/heartbreaker/src/main/index.ts`):

```
process.env.SPEDA_API_KEY  →  baked MAIN_VITE_SPEDA_API_KEY  →  'dev-key'
```

so a user-level environment variable wins over anything in the build, and the
same installer works for any server without a rebuild.

### The local escape hatch

For a build only you will ever run, `build-app.ps1` still bakes both values and
nothing is published:

```powershell
powershell -File build-app.ps1 -ApiBase https://your-server -ApiKey <key>
```

That installer must not be uploaded anywhere public.

## Signing

There is none. `electron-builder.yml` says so and the build log confirms
`no signing info identified, signing is skipped`. SmartScreen warns on first
run; that is expected for an unsigned in-house app.

## Known Windows failure: symlink extraction

Not a CI problem — CI runners hold the privilege — but it bites local builds.
`winCodeSign-2.6.0.7z` contains two macOS symlinks:

```
darwin/10.12/lib/libcrypto.dylib
darwin/10.12/lib/libssl.dylib
```

Creating a symlink on Windows needs `SeCreateSymbolicLinkPrivilege`, which a
normal shell lacks unless Developer Mode is on. 7-Zip extracts everything else
and still exits 2, so electron-builder discards the whole extraction and
retries forever, littering the cache with numbered temp directories.

The permanent fix is **Settings → System → For developers → Developer Mode**.
The one-off fix is to populate the cache by hand, skipping the folder that has
the symlinks — nothing in it is used on Windows:

```bash
7za x winCodeSign-2.6.0.7z -o"$LOCALAPPDATA/electron-builder/Cache/winCodeSign/winCodeSign-2.6.0" -x!darwin
```

Verify with `app-builder.exe download-artifact --name winCodeSign`: it should
print the cached path and exit 0 without downloading.
