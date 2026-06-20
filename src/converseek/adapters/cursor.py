"""Cursor adapter.

Reads from ~/Library/Application Support/Cursor/User/globalStorage/state.vscdb
  - cursorDiskKV table: composerData:* entries = conversations (JSON blobs)
  - bubbleId:* entries = individual messages
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ..base import BaseAdapter, SessionMeta, Message


CURSOR_DB = (
    Path.home()
    / "Library/Application Support/Cursor/User/globalStorage/state.vscdb"
)


class CursorAdapter(BaseAdapter):
    tool_name = "cursor"

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or CURSOR_DB

    def is_available(self) -> bool:
        return self.db_path.exists()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _extract_composer_info(self, key: str, value: str) -> tuple[SessionMeta, list[dict]] | None:
        """Parse a composerData entry into (meta, bubbles)."""
        composer_id = key.replace("composerData:", "")
        if not value:
            return None
        try:
            data = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None

        conversation = data.get("fullConversationHeadersOnly") or data.get("conversation") or []
        # Extract first user message as title
        title = ""
        first_ts = 0.0
        last_ts = 0.0
        first_user_bubble = ""
        for msg in conversation:
            if msg.get("type") == 1:  # user message
                text = msg.get("text", "")
                if text:
                    title = text[:200]
                    break
                # Text not inline — note the bubbleId for later lookup
                if not first_user_bubble:
                    first_user_bubble = msg.get("bubbleId", "")
        # Timestamps from createdAt fields
        for msg in conversation:
            ts_str = msg.get("createdAt", "")
            if ts_str:
                from datetime import datetime, timezone
                try:
                    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    ts = dt.timestamp()
                except Exception:
                    ts = 0.0
            else:
                ts = msg.get("timestamp", 0)
                if isinstance(ts, (int, float)):
                    ts = ts / 1000.0
            if ts and (first_ts == 0 or ts < first_ts):
                first_ts = ts
            if ts and ts > last_ts:
                last_ts = ts

        meta = SessionMeta(
            tool=self.tool_name,
            session_id=composer_id,
            title=title,
            source="cursor",
            created_at=first_ts if first_ts else 0.0,
            updated_at=last_ts if last_ts else first_ts if first_ts else 0.0,
            message_count=len(conversation),
            extra={"first_user_bubble": first_user_bubble},
        )
        return meta, conversation

    def _read_bubble(self, bubble_id: str, composer_id: str = "") -> str:
        """Read text content from a bubbleId:* entry.

        Key format: 'bubbleId:<composerId>:<bubbleId>' or 'bubbleId:<bubbleId>'
        """
        conn = self._connect()
        try:
            if composer_id:
                key = f"bubbleId:{composer_id}:{bubble_id}"
            else:
                key = f"bubbleId:{bubble_id}"
            row = conn.execute(
                "SELECT value FROM cursorDiskKV WHERE key = ?", (key,)
            ).fetchone()
            if not row or not row["value"]:
                # Fallback: try without composer_id prefix
                if composer_id:
                    row = conn.execute(
                        "SELECT value FROM cursorDiskKV WHERE key = ?",
                        (f"bubbleId:{bubble_id}",),
                    ).fetchone()
                if not row or not row["value"]:
                    return ""
            try:
                data = json.loads(row["value"])
            except (json.JSONDecodeError, TypeError):
                return ""
            # Check for text field
            text = data.get("text", "")
            if text:
                return text
            # Try richText (Lexical JSON)
            rich = data.get("richText", "")
            if rich:
                try:
                    return _extract_text_from_lexical(json.loads(rich))
                except (json.JSONDecodeError, TypeError):
                    pass
            return ""
        finally:
            conn.close()

    def list_sessions(
        self,
        *,
        limit: int = 50,
        since: float | None = None,
        cwd: str | None = None,
    ) -> list[SessionMeta]:
        # Cursor's composerData entries can be huge and numerous.
        # Use SQL to get keys + sizes first, only parse the most recent `limit`.
        conn = self._connect()
        try:
            # Only UUID-based composerData keys with actual content, skip 'task-*' stubs
            # and empty sessions. Sort by rowid DESC (most recent first).
            rows = conn.execute(
                "SELECT key, value FROM cursorDiskKV WHERE key LIKE 'composerData:%' "
                "AND key NOT LIKE 'composerData:task-%' "
                "AND value IS NOT NULL "
                "AND length(value) > 100 "
                "ORDER BY rowid DESC LIMIT ?",
                (limit * 3,)  # over-fetch since some may fail to parse
            ).fetchall()
            metas = []
            for row in rows:
                result = self._extract_composer_info(row["key"], row["value"])
                if not result:
                    continue
                meta, _ = result
                # If no inline title, try to fetch from bubbleId entry
                if not meta.title and meta.extra.get("first_user_bubble"):
                    bubble = self._read_bubble(meta.extra["first_user_bubble"], composer_id=meta.session_id)
                    if bubble:
                        meta.title = bubble[:200]
                if since and meta.created_at < since:
                    continue
                metas.append(meta)
                if len(metas) >= limit:
                    break
            metas.sort(key=lambda m: m.updated_at, reverse=True)
            return metas[:limit]
        finally:
            conn.close()

    def search_sessions(self, query: str, *, limit: int = 20) -> list[tuple[SessionMeta, str]]:
        conn = self._connect()
        try:
            q_lower = query.lower()
            # Use SQL LIKE to pre-filter, much faster than Python scan
            rows = conn.execute(
                "SELECT key, value FROM cursorDiskKV "
                "WHERE key LIKE 'composerData:%' AND key NOT LIKE 'composerData:task-%' "
                "AND value IS NOT NULL AND length(value) > 100 "
                "AND lower(value) LIKE ? ORDER BY rowid DESC LIMIT ?",
                (f"%{q_lower}%", limit * 2),
            ).fetchall()
            results = []
            for row in rows:
                result = self._extract_composer_info(row["key"], row["value"])
                if not result:
                    continue
                meta, conv = result
                # Find snippet from first matching message
                snippet = ""
                for msg in conv:
                    text = msg.get("text", "")
                    if text and q_lower in text.lower():
                        snippet = text[:300]
                        break
                if not snippet:
                    snippet = meta.title[:300]
                results.append((meta, snippet))
                if len(results) >= limit:
                    break
            return results[:limit]
        finally:
            conn.close()

    def get_session(self, session_id: str) -> SessionMeta | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT value FROM cursorDiskKV WHERE key = ?",
                (f"composerData:{session_id}",),
            ).fetchone()
            if not row:
                return None
            result = self._extract_composer_info(
                f"composerData:{session_id}", row["value"]
            )
            return result[0] if result else None
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
            row = conn.execute(
                "SELECT value FROM cursorDiskKV WHERE key = ?",
                (f"composerData:{session_id}",),
            ).fetchone()
            if not row:
                return []
            result = self._extract_composer_info(
                f"composerData:{session_id}", row["value"]
            )
            if not result:
                return []
            _, conversation = result
            messages = []
            for msg in conversation:
                role = "user" if msg.get("type") == 1 else "assistant"
                bubble_id = msg.get("bubbleId", "")
                ts_str = msg.get("createdAt", "")
                ts = 0.0
                if ts_str:
                    from datetime import datetime
                    try:
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
                    except Exception:
                        pass
                # Get text: try inline first, then bubbleId lookup
                text = msg.get("text", "")
                if not text and bubble_id:
                    text = self._read_bubble(bubble_id, composer_id=session_id)
                messages.append(Message(
                    msg_id=bubble_id,
                    role=role,
                    content=text,
                    timestamp=ts,
                ))
            conn.close()
            if window:
                return messages[-window:]
            return messages
        finally:
            conn.close()


def _extract_text_from_lexical(obj: dict) -> str:
    """Extract plain text from Lexical editor JSON (Cursor's richText format)."""
    texts = []
    def _walk(node):
        if isinstance(node, dict):
            if node.get("type") == "text":
                t = node.get("text", "")
                if t:
                    texts.append(t)
            for child in node.get("children", []):
                _walk(child)
        elif isinstance(node, list):
            for item in node:
                _walk(item)
    _walk(obj)
    return "".join(texts)
