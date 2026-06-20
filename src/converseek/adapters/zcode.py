"""ZCode adapter.

Reads from ~/.zcode/cli/db/db.sqlite — session + message tables.
Also has rollout JSONL files at ~/.zcode/cli/rollout/model-io-sess_*.jsonl
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ..base import BaseAdapter, SessionMeta, Message


ZCODE_DB = Path.home() / ".zcode" / "cli" / "db" / "db.sqlite"


class ZCodeAdapter(BaseAdapter):
    tool_name = "zcode"

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or ZCODE_DB

    def is_available(self) -> bool:
        return self.db_path.exists()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def list_sessions(
        self,
        *,
        limit: int = 50,
        since: float | None = None,
        cwd: str | None = None,
    ) -> list[SessionMeta]:
        conn = self._connect()
        try:
            sql = "SELECT * FROM session WHERE 1=1"
            params: list = []
            if since:
                sql += " AND time_created >= ?"
                params.append(since * 1000)  # ZCode uses ms
            if cwd:
                sql += " AND directory LIKE ?"
                params.append(f"{cwd}%")
            sql += " ORDER BY time_created DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_meta(r) for r in rows]
        finally:
            conn.close()

    def search_sessions(self, query: str, *, limit: int = 20) -> list[tuple[SessionMeta, str]]:
        conn = self._connect()
        try:
            # Search in part.data (text content), same as OpenCode
            sql = """
                SELECT s.*, substr(GROUP_CONCAT(substr(p.data, 1, 200), ' ... '), 1, 400) as snippet
                FROM part p
                JOIN session s ON s.id = p.session_id
                WHERE p.data LIKE ?
                GROUP BY s.id
                ORDER BY s.time_created DESC LIMIT ?
            """
            rows = conn.execute(sql, (f"%{query}%", limit)).fetchall()
            return [(self._row_to_meta(r), (r["snippet"] or "")[:400]) for r in rows]
        finally:
            conn.close()

    def get_session(self, session_id: str) -> SessionMeta | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM session WHERE id = ?", (session_id,)
            ).fetchone()
            return self._row_to_meta(row) if row else None
        finally:
            conn.close()

    def read_messages(
        self,
        session_id: str,
        *,
        window: int | None = None,
        around_msg_id: str | None = None,
    ) -> list[Message]:
        conn = self._connect()
        try:
            sql = "SELECT * FROM message WHERE session_id = ? ORDER BY time_created"
            if window:
                sql += f" DESC LIMIT {int(window)}"
                rows = conn.execute(sql, (session_id,)).fetchall()
                rows = list(reversed(rows))
            else:
                rows = conn.execute(sql, (session_id,)).fetchall()

            messages = []
            for row in rows:
                data_str = row["data"] or "{}"
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    data = {}
                role = data.get("role", "unknown")
                model = data.get("modelID")
                # Get text content from parts table
                content_parts = []
                part_rows = conn.execute(
                    "SELECT data FROM part WHERE message_id = ? ORDER BY time_created",
                    (row["id"],),
                ).fetchall()
                for pr in part_rows:
                    try:
                        pd = json.loads(pr["data"])
                    except json.JSONDecodeError:
                        continue
                    if pd.get("type") == "text":
                        content_parts.append(pd.get("text", ""))
                content = "\n".join(content_parts) if content_parts else ""
                messages.append(Message(
                    msg_id=row["id"],
                    role=role,
                    content=content,
                    timestamp=(row["time_created"] or 0) / 1000.0,
                    model=model,
                ))
            return messages
        finally:
            conn.close()

    def _row_to_meta(self, row: sqlite3.Row) -> SessionMeta:
        created_ms = row["time_created"] or 0
        updated_ms = row["time_updated"] or created_ms
        return SessionMeta(
            tool=self.tool_name,
            session_id=row["id"],
            title=row["title"] or "",
            source="cli",
            cwd=row["directory"],
            created_at=created_ms / 1000.0,
            updated_at=updated_ms / 1000.0,
        )
