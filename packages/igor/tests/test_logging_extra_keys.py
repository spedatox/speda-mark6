# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""No log call may pass a reserved LogRecord attribute through `extra=`.

`logging` builds every record by writing `extra`'s keys onto a LogRecord, and it
raises KeyError when one of them collides with a field the record already owns —
`name`, `module`, `filename`, `args`, and the rest below. The failure is
particularly nasty because it only fires on the path that logs: the upload
succeeded, the row was committed, and then the receipt line took the request
down with a 500. It was found exactly that way (projects.py's "name": filename),
alongside four older instances of the same mistake, two of them on the chat
attachment error path where an unreadable file would have killed the whole turn
instead of degrading to a note.

A repo-wide scan rather than a per-call-site test: the mistake is easy to make
in any new log line, and the only useful guard is one that sees all of them.
"""

import pathlib
import re

# The attributes logging.LogRecord assigns itself. Passing any of these in
# `extra` raises "Attempt to overwrite %r in LogRecord".
RESERVED = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "asctime",
}

APP = pathlib.Path(__file__).resolve().parent.parent / "app"
_EXTRA = re.compile(r"extra=\{([^}]*)\}", re.S)
_KEY = re.compile(r'"(\w+)"\s*:')


def test_no_reserved_keys_in_logging_extra():
    offenders: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for block in _EXTRA.finditer(source):
            line = source[: block.start()].count("\n") + 1
            for key in _KEY.findall(block.group(1)):
                if key in RESERVED:
                    offenders.append(f"{path.relative_to(APP.parent)}:{line} → extra={{'{key}': …}}")
    assert not offenders, (
        "These log calls pass a reserved LogRecord attribute through extra=, "
        "which raises at log time and 500s the request:\n  " + "\n  ".join(offenders)
    )
