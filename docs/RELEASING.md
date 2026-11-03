# Renovated delivery

The `Renovated CI/CD` workflow in `.github/workflows/validate.yml` owns validation,
production deployment and Heartbreaker Windows releases. Pull requests validate
and package the client; production credentials are used only for pushes to `main`
or a manual run selected on `main`.

## Release sequence

1. Build the pinned production runtime and verification image. Run the backend
   contracts against SQLite and disposable PostgreSQL, then real Docker isolation
   and teardown checks.
2. Typecheck both desktop clients, exercise shared worker behavior, and build
   their Electron and web bundles.
3. Build a Windows x64 NSIS installer for Heartbreaker and retain its checksum
   and source commit. Packaging must pass before production deployment starts.
4. Transfer the exact production image from that run over verified SSH. The
   installed host command checks the archive digest, every root filesystem layer,
   runtime configuration and source-commit label, then records the host's immutable
   image ID. This also handles image-ID normalization between Docker stores.
   Production data and credentials never come from CI.
5. Close the application write gate, drain active work, and stop the existing n8n
   scheduler and application. Retain a complete SQLite backup and private config
   snapshot. Rehearse the additive schema with the new and previous images before
   applying it to the existing database. Verify original rows are unchanged.
6. Recreate only the application, verify authenticated access and readiness,
   restore n8n's previous running state, and reopen traffic. Retain both images,
   backups and the host deployment report.
7. Publish the Heartbreaker installer once backend validation, client checks and
   Windows packaging pass. Publication runs independently of the production
   restart, so an installer is available as soon as its validation completes.

Main-branch runs are serialized without cancelling an active deployment. A host
file lock also prevents overlapping manual and CI deployments. Active work is
allowed to finish; a busy backend defers the release instead of cancelling it.

## Private host configuration

The existing repository secrets `SSH_HOST`, `SSH_USER`, `SSH_KEY` and optional
`SSH_PORT` supply the dedicated Actions deploy identity. The owner's local SSH
private key is not copied into GitHub. `.github/production-host-key.pub` is the
verified **public host key**; strict verification remains enabled.

CI calls `sudo -n /usr/local/sbin/speda-renovated-deploy`. Its root-owned driver
and SQLite helper are installed under `/opt/speda-operations/cicd/scripts/`.
The private `/opt/speda-operations/cicd/config.json` selects the current compose
files, env file, database, state directory, network-facing application and proxy.
The driver changes only the selected image in the private deployment env file.
It retains the existing mounts, OAuth credentials, model/voice policies, Telegram
webhooks, source history, native memory, worker toolchain pins and sidecar stack.

Orion Spark remains stopped and disabled at the owner's request. Deployment
does not start it. An unexpected active watchdog blocks deployment for inspection.

## Failed deployments and rollback

A schema rehearsal failure restarts the previous application without changing
the live schema. If the new application fails its checks after an additive schema
upgrade, the previous image is restarted against the current database. Its
compatibility was checked during rehearsal. The driver never copies an old backup
over current data or private settings, including newly rotated credentials.

Unexpected changes to original rows leave writers stopped and the public gate
closed for reconciliation. If neither application image becomes healthy, the
gate stays closed. Private command logs and database/config backups remain in
the host's deployment evidence directory; CI reports the deployment failure.
Installer publication has its own successful-build gate. Destructive schema
migrations require a separate reviewed cutover.

## Heartbreaker builds

Installers are attached to [GitHub Releases](https://github.com/spedatox/speda-mark6/releases)
as `SPEDA-Mark-VI-<version>-setup.exe`, together with `SHA256SUMS` and
`heartbreaker-release.json`. The Renovated release series starts at `3.0.0`.
Later runs increment the highest exact semantic tag. Rerunning an already tagged
commit reuses that version. Manual dispatch supports patch, minor or major bumps.

The optional repository variable `SPEDA_API_BASE` supplies the public server
address; otherwise the installer uses the current production URL. No API key is
embedded. On first launch, enter the server address and owner key in the connection
screen. Heartbreaker saves this private connection under Electron's user-data
directory. The installed application can edit it through its connection settings.

Windows installers are currently unsigned. Code signing requires a separate
certificate configuration. The NSIS installer preserves the normal per-user
installation and offers a destination-directory choice.

Backend image archives are retained as Actions artifacts for three days.
Verification reports and Windows installers are also retained as workflow
artifacts; published releases are independent of artifact retention.
