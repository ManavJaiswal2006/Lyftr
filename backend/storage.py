import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path


class Storage:
    def __init__(self, url: str):
        prefix = "sqlite:///"
        if not url.startswith(prefix) or not url[len(prefix):]:
            raise ValueError("DATABASE_URL must use sqlite:/// and a database file")
        self.path = Path(url[len(prefix):]).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn

    def initialize(self):
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    system_prompt TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(id),
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    metadata TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS messages_session_id
                ON messages (session_id, id);
            """)

    def check(self):
        with self.connect() as conn:
            conn.execute("SELECT id FROM sessions LIMIT 1").fetchone()

    def history(self, session_id: str):
        with self.connect() as conn:
            conn.execute("BEGIN")
            session = conn.execute(
                "SELECT system_prompt FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            rows = conn.execute(
                "SELECT role, content, metadata FROM messages WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()

        messages = []
        for row in rows:
            message = {"role": row["role"], "content": row["content"]}
            if row["metadata"] is not None:
                message["metadata"] = json.loads(row["metadata"])
            messages.append(message)
        return session["system_prompt"] if session else None, messages

    def save_turn(self, session_id, prompt, message, reply, metadata):
        with self.connect() as conn:
            conn.execute("""
                INSERT INTO sessions (id, system_prompt) VALUES (?, ?)
                ON CONFLICT(id) DO UPDATE SET system_prompt = excluded.system_prompt
            """, (session_id, prompt))
            conn.executemany("""
                INSERT INTO messages (session_id, role, content, metadata, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, [
                (session_id, role, content, entry_metadata, datetime.now(timezone.utc).isoformat())
                for role, content, entry_metadata in [
                    ("user", message, None),
                    ("assistant", reply, json.dumps(metadata)),
                ]
            ])
