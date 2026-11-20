"""SQLite storage. Plain stdlib sqlite3; callers run these in a thread."""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    system_prompt TEXT,
    created_at TEXT NOT NULL
);
"""


def path_from_url(url: str) -> str:
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        raise ValueError("DATABASE_URL must look like sqlite:////data/app.db")
    return url[len(prefix):]  # sqlite:////data/app.db -> /data/app.db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, url: str):
        self.path = path_from_url(url)
        folder = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(folder, exist_ok=True)
        with self._conn() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def ping(self) -> bool:
        with self._conn() as c:
            c.execute("SELECT 1").fetchone()
        return True

    def get_system_prompt(self, session_id: str) -> str | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT system_prompt FROM sessions WHERE session_id=?", (session_id,)
            ).fetchone()
        return row["system_prompt"] if row else None

    def get_history(self, session_id: str, limit: int) -> list[dict]:
        """Last `limit` messages, oldest first, as LLM-ready dicts."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT role, content FROM (SELECT id, role, content FROM messages "
                "WHERE session_id=? ORDER BY id DESC LIMIT ?) ORDER BY id ASC",
                (session_id, limit),
            ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in rows]

    def get_messages(self, session_id: str) -> list[dict] | None:
        """Full history for the API, or None if the session has no messages."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT role, content, metadata FROM messages WHERE session_id=? ORDER BY id ASC",
                (session_id,),
            ).fetchall()
        if not rows:
            return None
        out = []
        for r in rows:
            item = {"role": r["role"], "content": r["content"]}
            if r["role"] == "assistant" and r["metadata"]:
                item["metadata"] = json.loads(r["metadata"])
            out.append(item)
        return out

    def save_exchange(self, session_id: str, user_text: str, reply: str,
                      metadata: dict, system_prompt: str | None) -> None:
        """Persist user + assistant messages atomically (only called on success)."""
        now = _now()
        with self._conn() as c:
            c.execute(
                "INSERT INTO sessions(session_id, system_prompt, created_at) VALUES(?,?,?) "
                "ON CONFLICT(session_id) DO UPDATE SET system_prompt="
                "COALESCE(excluded.system_prompt, sessions.system_prompt)",
                (session_id, system_prompt, now),
            )
            c.execute(
                "INSERT INTO messages(session_id, role, content, metadata, created_at) "
                "VALUES(?,?,?,?,?)", (session_id, "user", user_text, None, now))
            c.execute(
                "INSERT INTO messages(session_id, role, content, metadata, created_at) "
                "VALUES(?,?,?,?,?)",
                (session_id, "assistant", reply, json.dumps(metadata), _now()))
