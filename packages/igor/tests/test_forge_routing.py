# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

from app.legion.execution_routing import prefer_forge


def worker(text: str, *, workspace: bool = True) -> str | None:
    result = prefer_forge(text, has_workspace=workspace)
    return result.worker_id if result else None


def test_execution_intents_prefer_existing_forge_roles():
    assert worker("Fix the failing parser test and verify the fix.") == "forge_coder"
    assert worker("Implement this API change across the repository and run tests.") == "forge_coder"
    assert worker("Find why this Docker image fails to build and fix it.") == "forge_coder"
    assert worker("Update the deployment configuration and verify it.") == "forge_coder"
    assert worker("Assess this local application for security issues.") == "forge_pentester"
    assert worker("Inspect this repository for the bug and fix what is causing it.") == "forge_coder"
    assert worker("Review this repository and report concrete findings.") == "forge_reviewer"


def test_discussion_and_ambiguous_intents_do_not_force_forge():
    assert worker("Explain why Docker layer caching can cause this.") is None
    assert worker("What pattern should I use for this service?") is None
    assert worker("Explain SQL injection.") is None
    assert worker("Help me with deployment.") is None
    assert worker("Fix the failing parser test and verify the fix.", workspace=False) is None
