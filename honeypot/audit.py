"""Local-only, bounded capture and persistent request quotas (one worker)."""
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class Audit:
    def __init__(self, directory, max_bytes=10 * 1024 * 1024, backups=9):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        self.path = self.directory / "events.jsonl"
        self.max_bytes, self.backups = max_bytes, backups

    def event(self, kind, request_id, **fields):
        record = {"time": datetime.now(timezone.utc).isoformat(), "event": kind,
                  "request_id": request_id, **fields}
        line = (json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n").encode()
        if self.path.exists() and self.path.stat().st_size + len(line) > self.max_bytes:
            for index in range(self.backups, 0, -1):
                source = self.path if index == 1 else self.path.with_suffix(f".jsonl.{index - 1}")
                target = self.path.with_suffix(f".jsonl.{index}")
                if source.exists():
                    os.replace(source, target)
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "ab") as stream:
            stream.write(line)
            stream.flush()


class Quota:
    """Keep request counts; zero disables the corresponding limit."""
    def __init__(self, directory, per_day, total):
        self.db = sqlite3.connect(Path(directory) / "quota.sqlite3")
        self.db.execute("CREATE TABLE IF NOT EXISTS counts (day TEXT PRIMARY KEY, n INTEGER NOT NULL)")
        self.db.commit()
        self.per_day, self.total = per_day, total

    def reserve(self):
        day = datetime.now(timezone.utc).date().isoformat()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            daily = self.db.execute("SELECT n FROM counts WHERE day=?", (day,)).fetchone()
            lifetime = self.db.execute("SELECT COALESCE(SUM(n), 0) FROM counts").fetchone()[0]
            daily_exhausted = self.per_day > 0 and daily and daily[0] >= self.per_day
            total_exhausted = self.total > 0 and lifetime >= self.total
            if daily_exhausted or total_exhausted:
                self.db.rollback()
                return False
            self.db.execute("INSERT INTO counts VALUES (?, 1) ON CONFLICT(day) DO UPDATE SET n=n+1", (day,))
            self.db.commit()
            return True
        except BaseException:
            self.db.rollback()
            raise

    def close(self):
        self.db.close()
