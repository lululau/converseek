"""Claude Code adapter.

Reads from:
  - ~/.claude/projects/<encoded-path>/sessions-index.json  (session metadata)
  - ~/.claude/projects/<encoded-path>/<session-id>.jsonl   (full messages)
"""
from __future__ import annotations

import json
import os
import glob
from pathlib import Path

from ..base import BaseAdapter, SessionMeta, Message


CLAUDE_DIR = Path.home() / ".claude" / "projects"


def _ts(iso_str: str) -> float:
    """Parse ISO timestamp to Unix epoch seconds."""
    if not iso_str:
        return 0.0
    from datetime import datetime, timezone
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.timestamp()
    except Exception:
        return 0.0


class ClaudeCodeAdapter(BaseAdapter):
    tool_name = "claude-code"

    def __init__(self, base_dir: Path | None = None):
        self.base_dir = base_dir or CLAUDE_DIR

    def is_available(self) -> bool:
        return self.base_dir.is_dir()

    def _iter_index_files(self) -> Iterator[Path]:
        """Yield all sessions-index.json files."""
        yield from self.base_dir.glob("*/sessions-index.json")

    def _load_index(self, index_path: Path) -> list[dict]:
        try:
            with open(index_path) as f:
                data = json.load(f)
            return data.get("entries", [])
        except Exception:
            return []

    def _entry_to_meta(self, entry: dict) -> SessionMeta:
        project_path = entry.get("projectPath", "")
        return SessionMeta(
            tool=self.tool_name,
            session_id=entry["sessionId"],
            title=entry.get("firstPrompt", "")[:200],
            source="cli",
            cwd=project_path or None,
            created_at=_ts(entry.get("created", "")),
            updated_at=_ts(entry.get("modified", "")),
            message_count=entry.get("messageCount", 0),
            git_branch=entry.get("gitBranch"),
        )

    def list_sessions(
        self,
        *,
        limit: int = 50,
        since: float | None = None,
        cwd: str | None = None,
    ) -> list[SessionMeta]:
        results: list[SessionMeta] = []
        for index_path in self._iter_index_files():
            for entry in self._load_index(index_path):
                meta = self._entry_to_meta(entry)
                if since and meta.created_at < since:
                    continue
                if cwd and meta.cwd and not meta.cwd.startswith(cwd):
                    continue
                results.append(meta)
        results.sort(key=lambda m: m.updated_at, reverse=True)
        return results[:limit]

    def search_sessions(self, query: str, *, limit: int = 20) -> list[tuple[SessionMeta, str]]:
        """Search by scanning sessions-index.json firstPrompt + JSONL filenames."""
        q_lower = query.lower()
        results: list[tuple[SessionMeta, str]] = []

        # Phase 1: search in firstPrompt (fast)
        for index_path in self._iter_index_files():
            for entry in self._load_index(index_path):
                title = entry.get("firstPrompt", "")
                if q_lower in title.lower():
                    meta = self._entry_to_meta(entry)
                    snippet = title[:300]
                    results.append((meta, snippet))
                if len(results) >= limit:
                    return results

        # Phase 2: grep JSONL files (slower, but catches body content)
        if len(results) < limit:
            for jsonl_path in self.base_dir.glob("*/*.jsonl"):
                try:
                    with open(jsonl_path, encoding="utf-8", errors="replace") as f:
                        for line in f:
                            if q_lower in line.lower():
                                session_id = jsonl_path.stem
                                meta = self.get_session(session_id)
                                if meta:
                                    snippet = line[:300]
                                    tup = (meta, snippet)
                                    if tup not in results:
                                        results.append(tup)
                                break  # one match per file is enough
                except Exception:
                    continue
                if len(results) >= limit:
                    break

        return results[:limit]

    def get_session(self, session_id: str) -> SessionMeta | None:
        for index_path in self._iter_index_files():
            for entry in self._load_index(index_path):
                if entry.get("sessionId") == session_id:
                    return self._entry_to_meta(entry)
        # Fallback: construct from JSONL file
        jsonl_path = next(self.base_dir.glob(f"*/{session_id}.jsonl"), None)
        if jsonl_path:
            return SessionMeta(
                tool=self.tool_name,
                session_id=session_id,
                title="(no index)",
                source="cli",
                created_at=jsonl_path.stat().st_mtime,
                updated_at=jsonl_path.stat().st_mtime,
            )
        return None

    def read_messages(
        self,
        session_id: str,
        *,
        window: int | None = None,
        around_msg_id: str | None = None,
    ) -> list[Message]:
        jsonl_path = next(self.base_dir.glob(f"*/{session_id}.jsonl"), None)
        if not jsonl_path:
            return []

        messages: list[Message] = []
        with open(jsonl_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg_type = obj.get("type")
                if msg_type not in ("user", "assistant"):
                    continue
                msg_data = obj.get("message", {})
                content = msg_data.get("content", "")
                if isinstance(content, list):
                    parts = []
                    for part in content:
                        if isinstance(part, dict):
                            if part.get("type") == "text":
                                parts.append(part.get("text", ""))
                            elif part.get("type") == "thinking":
                                pass  # skip thinking blocks for readability
                        elif isinstance(part, str):
                            parts.append(part)
                    content = "\n".join(parts)
                messages.append(
                    Message(
                        msg_id=obj.get("uuid", ""),
                        role=msg_type,
                        content=content,
                        timestamp=_ts(obj.get("timestamp", "")),
                    )
                )

        if around_msg_id and messages:
            for i, msg in enumerate(messages):
                if msg.msg_id == around_msg_id:
                    start = max(0, i - (window or 5))
                    end = min(len(messages), i + (window or 5) + 1)
                    return messages[start:end]
        elif window:
            return messages[-window:]

        return messages
