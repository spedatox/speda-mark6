"""App-owned connection preferences, atomic persistence and redacted client views."""
import json
import os
from pathlib import Path
import tempfile
from app.config import _DATA_DIR

MASKED = '\u2022' * 8


class ConnectionSettings:
    def __init__(self, path=None):
        self.path = Path(path) if path else _DATA_DIR / 'connections.json'

    def read(self):
        data = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
        if not isinstance(data, dict):
            raise ValueError('Connection settings are malformed')
        return data

    def write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix='.connections-')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(data, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, self.path)
        finally:
            Path(tmp).unlink(missing_ok=True)

    @staticmethod
    def mask(record):
        out = dict(record)
        for field in ('env', 'headers'):
            out[field] = {key: MASKED if value else '' for key, value in record.get(field, {}).items()}
        return out
