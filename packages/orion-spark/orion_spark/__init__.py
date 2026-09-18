# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Orion Spark — Lightweight reliability watchdog for Speda Mark VI.

Monitors Speda from outside its primary runtime, executes deterministic
recovery via the Defibrillation Protocol, manages Last Known Good (LKG)
revisions and quarantined defective builds, and dispatches independent
incident notifications.
"""

__version__ = "0.1.0"
