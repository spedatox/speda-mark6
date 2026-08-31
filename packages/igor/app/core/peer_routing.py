# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Which machine does this work belong on?

Optimus is ONE agent. Its identity, memory, prompts and session history live in
this backend — the peer is stateless per turn (see ExternalAgentProxy), so the
machine a turn executes on is a transport detail, not a second Optimus. This
module is the only place that detail is decided.

The failure it exists to prevent, 2026-08-04: the owner picked a folder in the
desktop file dialog and asked Optimus to build a site "here". The literal
string `C:\\Users\\AREL TARIM\\Downloads\\Yeni klasör` reached a Linux process,
where `\\` and `:` are legal filename characters, so nothing failed — mkdir
created ONE directory named after the whole path and the site built cleanly
inside it. Optimus reported success. Silent success on the wrong machine is the
worst available outcome.

The first fix refused every Windows path outright. That was right while every
peer was Linux and wrong the moment one of them is the owner's PC, so the
question asked here is not "is this POSIX" but "which connected peer claims
this path" — and if none does, refuse rather than guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# A drive-letter path ("C:\…", "D:/…") or a UNC share ("\\server\share").
_WINDOWS_ABS = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")

WINDOWS = "windows"
LINUX = "linux"


@dataclass(frozen=True)
class PeerInfo:
    """One attached execution surface for an agent.

    `host` distinguishes peers of the SAME agent — it is not a second agent_id.
    `roots` is what the peer advertises it will work in; empty means the peer
    accepts any path well-formed for its platform, which is what the long-
    standing server peer does and why adding hosts changes nothing for it.
    """

    agent_id: str
    host: str = "default"
    platform: str = LINUX
    roots: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_windows(self) -> bool:
        return self.platform.lower().startswith("win")


def well_formed(path: str, platform: str) -> bool:
    """Is `path` an absolute path this platform could actually resolve?

    The check that catches a path aimed at the wrong OS even when the target
    peer advertises no roots to match against.
    """
    if not path:
        return False
    windows = platform.lower().startswith("win")
    if windows:
        return bool(_WINDOWS_ABS.match(path))
    # POSIX: absolute, and free of the two characters that make a Windows path
    # survive on Linux as a filename instead of failing.
    return path.startswith("/") and "\\" not in path and not _WINDOWS_ABS.match(path)


def _canonical(path: str, platform: str) -> str:
    """Case/separator-normalised form for prefix comparison only.

    Never returned to a caller or sent to a peer — the peer receives the
    original string, because it is the one that has to resolve it.
    """
    text = path.strip().rstrip("/\\")
    if platform.lower().startswith("win"):
        return text.replace("\\", "/").lower()
    return text


def claims(peer: PeerInfo, path: str) -> bool:
    """Would this peer accept work in `path`?

    A peer that advertises roots is bounded by them. A peer that advertises
    none accepts anything well-formed for its platform — this is the server
    peer's behaviour today and preserving it is what keeps the existing
    deployment working untouched while a second host is added.
    """
    if not well_formed(path, peer.platform):
        return False
    if not peer.roots:
        return True
    target = _canonical(path, peer.platform)
    for root in peer.roots:
        base = _canonical(root, peer.platform)
        if base and (target == base or target.startswith(base + "/")):
            return True
    return False


@dataclass(frozen=True)
class Resolution:
    """Where a turn should run, or why it cannot run anywhere."""

    host: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _describe(peers: list[PeerInfo]) -> str:
    """What IS connected, for a refusal that tells the owner where to go next."""
    if not peers:
        return "no peers are connected"
    parts = []
    for peer in peers:
        if peer.roots:
            parts.append(f"{peer.host} ({peer.platform}): " + ", ".join(peer.roots))
        else:
            parts.append(f"{peer.host} ({peer.platform}): any {peer.platform} path")
    return "; ".join(parts)


def default_host(peers: list[PeerInfo]) -> str | None:
    """The peer to use when the work names no directory.

    Always-on before convenient: a PC that may be asleep must never be the
    default for work that did not ask for it. Server peers advertise no roots,
    so "claims everything" doubles as "is the general-purpose one".
    """
    if not peers:
        return None
    for peer in peers:
        if not peer.roots and not peer.is_windows:
            return peer.host
    for peer in peers:
        if not peer.roots:
            return peer.host
    return peers[0].host


def resolve(peers: list[PeerInfo], cwd: str | None) -> Resolution:
    """Pick the peer for this turn, or refuse.

    1. No directory → the always-on peer. Most turns take this path.
    2. A directory some peer claims → that peer. The owner picks a folder and
       the folder identifies the machine; there is no mode to remember.
    3. A directory nobody claims → refuse immediately, naming what is
       connected. Never fall through to another peer: falling through is
       exactly how work lands on the wrong machine, which is the whole reason
       this module exists.
    """
    if not peers:
        return Resolution(error="No execution peer is connected, so there is "
                                "nowhere to run this. Do not retry until one "
                                "comes online.")

    path = (cwd or "").strip()
    if not path:
        return Resolution(host=default_host(peers))

    for peer in peers:
        if claims(peer, path):
            return Resolution(host=peer.host)

    return Resolution(error=(
        f"Refused: no connected peer works in {path!r}. Connected — "
        f"{_describe(peers)}. This is refused rather than sent to another "
        "machine, because a path that silently resolves somewhere else "
        "produces a finished job in the wrong place and a success report to "
        "match. Pick a directory one of these peers covers, or leave it unset "
        "to use the default peer's own workspace."
    ))
