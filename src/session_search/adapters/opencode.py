"""OpenCode adapter.

Reads from ~/.local/share/opencode/opencode.db (the main data store).
Schema: session + message + part tables. Messages have role info in JSON `data`;
actual text content is in the `part` table (type=text parts).

Note: There is also a legacy ~/.opencode/opencode.db with a simpler schema.
This adapter supports the newer opencode.ai schema.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ..base import BaseAdapter, SessionMeta, Message


# Primary DB (opencode.ai v2+ schema)
OPENCODE_DB = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
# Legacy DB (opencode-ai/opencode schema)
OPENCODE_DB_LEGACY = Path.home() / ".opencode" / "opencode.db"


class OpenCodeAdapter(BaseAdapter):
    tool_name = "opencode"

    def __init__(self, db_path: Path | None = None):
        if db_path:
            self.db_path = db_path
        elif OPENCODE_DB.exists():
            self.db_path = OPENCODE_DB
        else:
            self.db_path = OPENCODE_DB_LEGACY
        self._legacy = self.db_path == OPENCODE_DB_LEGACY

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
            if self._legacy:
                sql = "SELECT * FROM sessions WHERE 1=1"
                params: list = []
                if since:
                    sql += " AND created_at >= ?"
                    params.append(since * 1000)
                sql += " ORDER BY created_at DESC LIMIT ?"
                params.append(limit)
                rows = conn.execute(sql, params).fetchall()
                return [self._legacy_row_to_meta(r) for r in rows]

            # New schema
            sql = "SELECT * FROM session WHERE 1=1"
            params: list = []
            if since:
                sql += " AND time_created >= ?"
                params.append(since * 1000)
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
            if self._legacy:
                sql = """
                    SELECT s.*, substr(m.parts, 1, 300) as snippet
                    FROM messages m
                    JOIN sessions s ON s.id = m.session_id
                    WHERE m.parts LIKE ?
                    GROUP BY s.id
                    ORDER BY s.created_at DESC LIMIT ?
                """
                rows = conn.execute(sql, (f"%{query}%", limit)).fetchall()
                return [(self._legacy_row_to_meta(r), (r["snippet"] or "")[:400]) for r in rows]

            # New schema: search in part.data (text content)
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
            table = "sessions" if self._legacy else "session"
            row = conn.execute(
                f"SELECT * FROM {table} WHERE id = ?", (session_id,)
            ).fetchone()
            if self._legacy:
                return self._legacy_row_to_meta(row) if row else None
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
            if self._legacy:
                sql = "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at"
                if window:
                    sql += f" DESC LIMIT {int(window)}"
                    rows = conn.execute(sql, (session_id,)).fetchall()
                    rows = list(reversed(rows))
                else:
                    rows = conn.execute(sql, (session_id,)).fetchall()
                return [self._legacy_row_to_msg(r) for r in rows]

            # New schema: messages → parts (text content is in parts)
            sql = "SELECT * FROM message WHERE session_id = ? ORDER BY time_created"
            if window:
                sql += f" DESC LIMIT {int(window)}"
                rows = conn.execute(sql, (session_id,)).fetchall()
                rows = list(reversed(rows))
            else:
                rows = conn.execute(sql, (session_id,)).fetchall()

            messages = []
            for row in rows:
                data = json.loads(row["data"] or "{}")
                role = data.get("role", "unknown")
                # Get text from parts
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
                    model=data.get("model"),
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

    def _legacy_row_to_meta(self, row: sqlite3.Row) -> SessionMeta:
        created_ms = row["created_at"] or 0
        updated_ms = row["updated_at"] or created_ms
        return SessionMeta(
            tool=self.tool_name,
            session_id=row["id"],
            title=row["title"] or "",
            source="cli",
            created_at=created_ms / 1000.0,
            updated_at=updated_ms / 1000.0,
            message_count=row["message_count"] or 0,
        )

    def _legacy_row_to_msg(self, row: sqlite3.Row) -> Message:
        content = ""
        parts_raw = row["parts"] or "[]"
        try:
            parts = json.loads(parts_raw)
            texts = []
            for part in parts:
                if isinstance(part, dict):
                    texts.append(part.get("text", ""))
                elif isinstance(part, str):
                    texts.append(part)
            content = "\n".join(texts)
        except (json.JSONDecodeError, TypeError):
            content = parts_raw[:500]
        return Message(
            msg_id=row["id"],
            role=row["role"],
            content=content,
            timestamp=(row["created_at"] or 0) / 1000.0,
        )
