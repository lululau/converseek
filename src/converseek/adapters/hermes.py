"""Hermes adapter.

Reads from ~/.hermes/state.db — sessions + messages tables with FTS5.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from ..base import BaseAdapter, SessionMeta, Message


HERMES_DB = Path.home() / ".hermes" / "state.db"


class HermesAdapter(BaseAdapter):
    tool_name = "hermes"

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or HERMES_DB

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
            sql = "SELECT * FROM sessions WHERE 1=1"
            params: list = []
            if since:
                sql += " AND started_at >= ?"
                params.append(since)
            if cwd:
                sql += " AND cwd LIKE ?"
                params.append(f"{cwd}%")
            sql += " ORDER BY started_at DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_meta(r) for r in rows]
        finally:
            conn.close()

    def search_sessions(self, query: str, *, limit: int = 20) -> list[tuple[SessionMeta, str]]:
        conn = self._connect()
        try:
            # FTS5 search on messages, then aggregate to sessions
            sql = """
                SELECT s.*, GROUP_CONCAT(substr(m.content, 1, 200), ' ... ') as snippet
                FROM messages_fts fts
                JOIN messages m ON m.id = fts.rowid
                JOIN sessions s ON s.id = m.session_id
                WHERE messages_fts MATCH ?
                GROUP BY s.id
                ORDER BY s.started_at DESC
                LIMIT ?
            """
            # Escape FTS5 special chars: wrap query as phrase
            fts_query = " OR ".join(query.split())
            rows = conn.execute(sql, (fts_query, limit)).fetchall()
            results = []
            for row in rows:
                meta = self._row_to_meta(row)
                snippet = row["snippet"] or ""
                results.append((meta, snippet[:400]))
            return results
        except Exception:
            # FTS might fail on special syntax, fallback to LIKE
            sql = """
                SELECT s.*, substr(m.content, 1, 200) as snippet
                FROM messages m
                JOIN sessions s ON s.id = m.session_id
                WHERE m.content LIKE ?
                GROUP BY s.id
                ORDER BY s.started_at DESC
                LIMIT ?
            """
            rows = conn.execute(sql, (f"%{query}%", limit)).fetchall()
            return [(self._row_to_meta(r), (r["snippet"] or "")[:400]) for r in rows]
        finally:
            conn.close()

    def get_session(self, session_id: str) -> SessionMeta | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
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
            if around_msg_id:
                try:
                    anchor_id = int(around_msg_id)
                except ValueError:
                    anchor_id = None
                if anchor_id:
                    sql = """
                        SELECT * FROM (
                            SELECT * FROM messages WHERE session_id = ? AND id < ? ORDER BY id DESC LIMIT ?
                        ) UNION ALL SELECT * FROM (
                            SELECT * FROM messages WHERE session_id = ? AND id >= ? ORDER BY id ASC LIMIT ?
                        )
                    """
                    w = window or 5
                    rows = conn.execute(
                        sql, (session_id, anchor_id, w, session_id, anchor_id, w + 1)
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM messages WHERE session_id = ? ORDER BY id",
                        (session_id,),
                    ).fetchall()
            else:
                if window:
                    sql = "SELECT * FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?"
                    rows = conn.execute(sql, (session_id, window)).fetchall()
                    rows = list(reversed(rows))
                else:
                    rows = conn.execute(
                        "SELECT * FROM messages WHERE session_id = ? ORDER BY id",
                        (session_id,),
                    ).fetchall()
            return [self._row_to_msg(r) for r in rows]
        finally:
            conn.close()

    def _row_to_meta(self, row: sqlite3.Row) -> SessionMeta:
        return SessionMeta(
            tool=self.tool_name,
            session_id=row["id"],
            title=row["title"] or "",
            source=row["source"] or "",
            cwd=row["cwd"],
            created_at=row["started_at"] or 0.0,
            updated_at=row["started_at"] or 0.0,
            message_count=row["message_count"] or 0,
            model=row["model"],
        )

    def _row_to_msg(self, row: sqlite3.Row) -> Message:
        return Message(
            msg_id=str(row["id"]),
            role=row["role"],
            content=row["content"] or "",
            timestamp=row["timestamp"] or 0.0,
            tool_name=row["tool_name"],
            reasoning=row["reasoning_content"],
        )
