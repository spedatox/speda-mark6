# Skyfall Protocol — plan & design record

**Status:** DESIGN ONLY — nothing built yet. This captures what we established so it
can be built from later. · **Drafted:** 2026-07-25

---

## Why Skyfall exists

Mark VI is about to go **private** and ship as a **compiled Igor binary** onto machines
the owner does not control. Skyfall is the **kill switch + license spine** for that
distribution. Three capabilities:

1. **Fleet visibility** — how many Igor instances are alive, which versions.
2. **Remote deactivation** — the owner picks a specific instance, confirms, and it
   stops **permanently**.
3. **Enforcement** — an unauthorized copy cannot just run forever.

### The inviolable safety invariant

Skyfall **only ever stops Igor from running.** It never touches anything else on the
machine — no file deletion, no data wipe, no OS or settings changes. "Scorched earth"
is scoped strictly to Igor's *own* ability to operate (its own process, its own
sockets, its own in-memory secrets), nothing beyond its own footprint. This mirrors
the `system_ops` deny-list philosophy already in the codebase. No harm, no damage to
the host — the instance simply stops working.

---

## The core insight

**A "kill packet" alone is weak.** If Igor runs happily until it *receives* a stop
signal, anyone can firewall the phone-home and the kill never arrives.

The robust design inverts it: **Igor needs a fresh, server-signed lease to keep
running.** The kill is not a packet you send — it is a renewal you **withhold**. Block
the network to dodge the kill, and it dies on its own when the lease lapses. That is
the line between DRM theater and something that actually works.

Corollary — **need the server, don't just check it.** A license *check* can be patched
out of a binary on someone else's machine. So Igor is made to *structurally depend* on
the live server for something it cannot fake (see model-token brokering below). Then
patching the check gains nothing, because the thing they cracked wasn't what was
stopping them.

---

## Decisions established

| # | Decision | Choice |
|---|----------|--------|
| 1 | Enforcement model | **Fail-closed signed lease** (not a fail-open kill signal) |
| 2 | Lease signing | **Ed25519** — private key on the mothership only; public key embedded in the binary |
| 3 | Replay defense | **Challenge–response nonce** (defeats a fake local server replaying a captured lease) **+ short TTL** (defeats offline replay) **+ clock-rollback pinning** (persist highest `issued_at` seen, reject older) |
| 4 | Model access | **Short-lived model tokens** brokered by the mothership — the instance talks to the model **directly** (keeps the owner's server idle: it handles only tiny lease/heartbeat traffic, never model payloads) |
| 5 | Model API key | **NEVER embedded** in the distributed binary (extractable = account theft). The mothership issues short-lived, scoped model tokens instead |
| 6 | Revoke latency | **= model-token TTL.** Keep TTL short (**15–30 min**) so a revoke lands fast while the server stays idle |
| 7 | Offline grace | Lease default **72 h** offline tolerance for legit users. Grace cannot be *exploited* (leases are unforgeable); it only trades legit-user offline tolerance vs. how fast a revoke lands |
| 8 | Telemetry scope | **Minimal** — `instance_id`, version, uptime, `last_seen`. Never chat data, never owner data |
| 9 | EULA | Disclose phone-home + remote deactivation plainly. Not about readership — it is the owner's legal footing. One paragraph |

---

## Architecture — two halves that MUST stay separate

**The Mothership** — runs only on infra the owner controls (the existing Contabo prod,
or a small dedicated service — *undecided*). Holds:
- the **private** Ed25519 signing key
- the instance registry (the fleet)
- the admin controls (list / revoke / reinstate)
- model-token issuance
- **Never distributed.**

**Distributed Igor** — the compiled binary. Embeds only:
- the mothership URL
- the owner's **public** key (can *verify* leases, cannot *forge* them)
- the phone-home client
- **No model API key. No private key.**

### How the three capabilities fall out

- **Identity** — first boot: generate a persistent `instance_id` (UUID) + a coarse,
  salted machine fingerprint (non-PII). Register with the mothership.
- **Fleet count** — heartbeat every ~15 min (`instance_id`, version, uptime). Mothership
  tracks `last_seen`. "Running now" = seen within the last window.
- **Skyfall kill** — each instance has a status `active | revoked`. The owner flips the
  chosen instance to `revoked` (explicit confirm).
  - **Fast path:** live connection → push revoke → immediate graceful stop.
  - **Fail-safe:** the lease / model token stops renewing → the instance halts within
    the grace window even if it is dodging the push.
  - **On revoke:** write a signed local tombstone, tear down Igor's own peers/sockets,
    wipe its own in-memory secrets, exit, and refuse to start again.

### Kill flow (worst case = one grace/TTL window)

```
Owner clicks "Deactivate" + confirms
        │
        ▼
Mothership sets instance.status = revoked
        │
        ├──▶ (live conn)  push revoke ──▶ Igor: graceful stop NOW
        │
        └──▶ (offline/dodging)  stop issuing lease + model token
                                        │
                                 lease/token expires (≤ TTL)
                                        │
                                        ▼
                              Igor halts, writes tombstone, refuses restart
```

---

## Honest limits

- **Compiled ≠ uncrackable.** A determined attacker can patch the binary. Fail-closed
  leasing beats the *easy* evasion (firewalling); model-token brokering beats the
  *check-patch* evasion (a cracked client still can't reach the model). Serious
  tamper-resistance (obfuscation, integrity self-checks) is a further optional layer.
- This is **strong deterrence, not perfect DRM** — and for stopping unauthorized *use*
  (casual copying, expired customers), it is more than enough.

---

## Open decisions (still to make before building)

1. **Control surface** — how the owner watches the fleet and triggers a kill:
   - Admin API + small CLI (fastest, no UI)
   - Desktop dashboard in Heartbreaker/Striker
   - Private web dashboard
   *(Deferred — was raised, not yet chosen.)*
2. **Licensing granularity** — per-instance registration (gifting specific instances)
   vs. **per-seat license keys** (each key = N seats) if Igor will actually be *sold*.
3. **Where the mothership runs** — reuse the Contabo prod backend vs. a dedicated
   minimal license service.
4. **Whether there's a customer-facing app** at all (owner undecided).
5. Concrete data model, endpoint contract, token format, tombstone format.

---

## Suggested build phases (when we proceed)

1. **Mothership registry + heartbeat** — `instances` table, `POST /license/register`,
   `POST /license/heartbeat`, admin `GET /license/instances`. Delivers fleet visibility.
2. **Signed lease + nonce** — Ed25519 issuance/verification, challenge-response,
   TTL + clock-rollback pinning. Delivers fail-closed enforcement.
3. **Model-token brokering** — mothership issues short-lived scoped model tokens in the
   lease response; distributed Igor stops embedding any model key. Delivers real teeth.
4. **Revoke path** — admin `POST /license/instances/{id}/revoke` (confirm-gated) + the
   fast push + the client tombstone/self-stop. Delivers the kill switch.
5. **Control surface** — build the chosen option from open-decision #1 on top of the
   admin API.
6. **EULA + hardening** — disclosure paragraph; optional obfuscation/integrity checks.

---

*Naming note: sibling to the **Lifeboat Protocol** (`docs/LIFEBOAT_PROTOCOL.md`) — the
disk-survival runbook. Lifeboat keeps the owner's own host alive; Skyfall governs
distributed instances the owner does not host.*
