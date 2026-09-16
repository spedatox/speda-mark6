# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Deterministic preference policy for existing Forge execution roles."""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class ForgePreference:
    worker_id: str
    reason: str


_DISCUSSION = re.compile(
    r"^(?:please\s+)?(explain|what(?:'s| is| are)?|how does|how do|why does|recommend|advice|"
    r"which pattern|what pattern|teach me|conceptually)\b", re.IGNORECASE,
)
_EXECUTE = re.compile(
    r"\b(fix|implement|change|modify|update|upgrade|refactor|debug|run|execute|"
    r"test|verify|build|deploy|prepare|configure|inspect|investigate|find why|"
    r"assess|audit|review)\b", re.IGNORECASE,
)
_WORKSPACE = re.compile(
    r"\b(repo(?:sitory)?|codebase|project|workspace|files?|tests?|parser|api|"
    r"docker(?:file)?|image|container|deployment|deploy|ci/?cd|pipeline|"
    r"infrastructure|config(?:uration)?|application|app|dependencies|build)\b",
    re.IGNORECASE,
)
_SECURITY = re.compile(
    r"\b(security|secure|vulnerabilit(?:y|ies)|pentest|penetration|threat|"
    r"dependency audit|authorized)\b", re.IGNORECASE,
)
_LOCAL_SCOPE = re.compile(
    r"\b(local|this|repository|repo|codebase|project|workspace|application|app)\b",
    re.IGNORECASE,
)
_MUTATION = re.compile(
    r"\b(fix|implement|change|modify|update|upgrade|refactor|write|edit|build|"
    r"deploy|configure)\b", re.IGNORECASE,
)
_REVIEW = re.compile(r"\b(review|inspect|audit)\b", re.IGNORECASE)


def prefer_forge(message: str, *, has_workspace: bool) -> ForgePreference | None:
    """Prefer Forge only for clear action + workspace intent; ambiguity stays model-owned."""
    text = " ".join(message.split())
    if not text or not has_workspace or _DISCUSSION.search(text):
        return None
    if not (_EXECUTE.search(text) and _WORKSPACE.search(text)):
        return None
    if _SECURITY.search(text) and _LOCAL_SCOPE.search(text):
        return ForgePreference("forge_pentester", "authorized_local_security_execution")
    if _REVIEW.search(text) and not _MUTATION.search(text):
        return ForgePreference("forge_reviewer", "repository_review_execution")
    return ForgePreference("forge_coder", "workspace_engineering_execution")
