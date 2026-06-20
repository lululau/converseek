"""Paseo adapter.

Reads from ~/.paseo/agents/<encoded-path>/<session-id>.json
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ..base import BaseAdapter, SessionMeta, Message


PASEO_DIR = Path.home() / ".paseo" / "agents"


def _parse_iso(iso_str: str) -> float:
    if not iso_str:
        return 0.0
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.timestamp()
    except Exception:
        return 0.0


class PaseoAdapter(BaseAdapter):
    tool_name = "paseo"

    def __init__(self, base_dir: Path | None = None):
        self.base_dir = base_dir or PASEO_DIR

    def is_available(self) -> bool:
        return self.base_dir.is_dir()

    def _iter_session_files(self) -> list[tuple[Path, dict]]:
        """Yield (path, parsed_json) for all session JSON files."""
        results = []
        for json_path in self.base_dir.glob("*/*.json"):
            try:
                with open(json_path) as f:
                    data = json.load(f)
                results.append((json_path, data))
            except (json.JSONDecodeError, IOError):
                continue
        return results

    def _json_to_meta(self, data: dict) -> SessionMeta:
        return SessionMeta(
            tool=self.tool_name,
            session_id=data.get("id", ""),
            title=data.get("title", ""),
            source="cli",
            cwd=data.get("cwd"),
            created_at=_parse_iso(data.get("createdAt", "")),
            updated_at=_parse_iso(data.get("updatedAt", "")),
            model=data.get("config", {}).get("model"),
        )

    def list_sessions(
        self,
        *,
        limit: int = 50,
        since: float | None = None,
        cwd: str | None = None,
    ) -> list[SessionMeta]:
        metas = []
        for _, data in self._iter_session_files():
            meta = self._json_to_meta(data)
            if since and meta.created_at < since:
                continue
            if cwd and meta.cwd and not meta.cwd.startswith(cwd):
                continue
            metas.append(meta)
        metas.sort(key=lambda m: m.updated_at, reverse=True)
        return metas[:limit]

    def search_sessions(self, query: str, *, limit: int = 20) -> list[tuple[SessionMeta, str]]:
        q_lower = query.lower()
        results = []
        for _, data in self._iter_session_files():
            # Search in title and labels
            title = data.get("title", "")
            if q_lower in title.lower():
                meta = self._json_to_meta(data)
                results.append((meta, title[:300]))
            if len(results) >= limit:
                break
        return results[:limit]

    def get_session(self, session_id: str) -> SessionMeta | None:
        for _, data in self._iter_session_files():
            if data.get("id") == session_id:
                return self._json_to_meta(data)
        return None

    def read_messages(self, session_id: str, **kwargs) -> list[Message]:
        # Paseo stores metadata only, not full transcripts in the agent JSON.
        # Full transcripts may be in the persistence.nativeHandle path.
        return []
