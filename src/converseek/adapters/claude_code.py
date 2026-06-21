"""Claude Code adapter.

Source of truth is the per-session JSONL transcript:
  - ~/.claude/projects/<encoded-path>/<session-id>.jsonl   (full messages)

The legacy ~/.claude/projects/<encoded-path>/sessions-index.json file is only
used as *optional* enrichment when present. Newer Claude Code versions no
longer keep that index up to date (and most projects never have one), so
relying on it alone hides nearly every session. We therefore scan the JSONL
files directly and fall back to the index purely for nicer titles/summaries.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from ..base import BaseAdapter, SessionMeta, Message


CLAUDE_DIR = Path.home() / ".claude" / "projects"


def _ts(iso_str: str) -> float:
    """Parse ISO timestamp to Unix epoch seconds."""
    if not iso_str:
        return 0.0
    from datetime import datetime
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.timestamp()
    except Exception:
        return 0.0


def _content_to_text(content) -> str:
    """Flatten a Claude message ``content`` field into plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text":
                    parts.append(part.get("text", ""))
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(p for p in parts if p)
    return ""


class ClaudeCodeAdapter(BaseAdapter):
    tool_name = "claude"

    def __init__(self, base_dir: Path | None = None):
        self.base_dir = base_dir or CLAUDE_DIR

    def is_available(self) -> bool:
        return self.base_dir.is_dir()

    # ------------------------------------------------------------------
    # Optional legacy index (enrichment only)
    # ------------------------------------------------------------------
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

    def _index_map(self) -> dict[str, dict]:
        """Map sessionId -> index entry across all index files."""
        mapping: dict[str, dict] = {}
        for index_path in self._iter_index_files():
            for entry in self._load_index(index_path):
                sid = entry.get("sessionId")
                if sid:
                    mapping.setdefault(sid, entry)
        return mapping

    # ------------------------------------------------------------------
    # JSONL scanning (source of truth)
    # ------------------------------------------------------------------
    def _iter_jsonl_files(self) -> Iterator[Path]:
        yield from self.base_dir.glob("*/*.jsonl")

    def _extract_meta(
        self, jsonl_path: Path, index_entry: dict | None = None
    ) -> SessionMeta:
        """Build a SessionMeta by scanning a session JSONL transcript."""
        session_id = jsonl_path.stem
        cwd: str | None = None
        git_branch: str | None = None
        first_prompt = ""
        first_ts = 0.0
        last_ts = 0.0
        message_count = 0
        model: str | None = None

        try:
            with open(jsonl_path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if cwd is None and obj.get("cwd"):
                        cwd = obj.get("cwd")
                    if git_branch is None and obj.get("gitBranch"):
                        git_branch = obj.get("gitBranch")
                    if obj.get("type") not in ("user", "assistant"):
                        continue
                    msg = obj.get("message", {}) or {}
                    text = _content_to_text(msg.get("content", "")).strip()
                    if not text:
                        continue
                    message_count += 1
                    ts = _ts(obj.get("timestamp", ""))
                    if ts:
                        if not first_ts:
                            first_ts = ts
                        last_ts = ts
                    if model is None and msg.get("model"):
                        model = msg.get("model")
                    if not first_prompt and obj.get("type") == "user":
                        first_prompt = text
        except OSError:
            pass

        try:
            mtime = jsonl_path.stat().st_mtime
        except OSError:
            mtime = 0.0

        if index_entry:
            if not first_prompt:
                first_prompt = index_entry.get("firstPrompt", "") or index_entry.get(
                    "summary", ""
                )
            if cwd is None:
                cwd = index_entry.get("projectPath") or None
            if git_branch is None:
                git_branch = index_entry.get("gitBranch")

        return SessionMeta(
            tool=self.tool_name,
            session_id=session_id,
            title=(first_prompt or "")[:200],
            source="cli",
            cwd=cwd,
            created_at=first_ts or mtime,
            updated_at=last_ts or mtime,
            message_count=message_count,
            model=model,
            git_branch=git_branch,
        )

    def list_sessions(
        self,
        *,
        limit: int = 50,
        since: float | None = None,
        cwd: str | None = None,
    ) -> list[SessionMeta]:
        index_map = self._index_map()
        results: list[SessionMeta] = []
        for jsonl_path in self._iter_jsonl_files():
            meta = self._extract_meta(jsonl_path, index_map.get(jsonl_path.stem))
            if since and meta.updated_at < since:
                continue
            if cwd and (not meta.cwd or not meta.cwd.startswith(cwd)):
                continue
            results.append(meta)
        results.sort(key=lambda m: m.updated_at, reverse=True)
        return results[:limit]

    def search_sessions(self, query: str, *, limit: int = 20) -> list[tuple[SessionMeta, str]]:
        """Search by scanning JSONL transcripts (title first, then body)."""
        q_lower = query.lower()
        index_map = self._index_map()
        results: list[tuple[SessionMeta, str]] = []
        seen: set[str] = set()

        for jsonl_path in self._iter_jsonl_files():
            sid = jsonl_path.stem
            if sid in seen:
                continue
            meta = self._extract_meta(jsonl_path, index_map.get(sid))
            # Phase 1: match on title.
            if q_lower in meta.title.lower():
                results.append((meta, meta.title[:300]))
                seen.add(sid)
                if len(results) >= limit:
                    return results
                continue
            # Phase 2: scan body for a matching line.
            try:
                with open(jsonl_path, encoding="utf-8", errors="replace") as f:
                    for line in f:
                        if q_lower in line.lower():
                            results.append((meta, line.strip()[:300]))
                            seen.add(sid)
                            break
            except OSError:
                continue
            if len(results) >= limit:
                break

        return results[:limit]

    def get_session(self, session_id: str) -> SessionMeta | None:
        jsonl_path = next(self.base_dir.glob(f"*/{session_id}.jsonl"), None)
        if jsonl_path:
            return self._extract_meta(jsonl_path, self._index_map().get(session_id))
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
                if not content.strip():
                    continue
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
