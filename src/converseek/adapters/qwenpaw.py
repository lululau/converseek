"""QwenPaw / Copaw adapter.

Reads from qwenpaw agent workspace directory:
  - chats.json (chat/session index)
  - sessions/{channel}/{user_id}_{session_id}.json or sessions/{session_id}.json (chat history)
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from ..base import BaseAdapter, SessionMeta, Message


def sanitize_filename(name: str) -> str:
    """Replace characters that are illegal in Windows filenames with ``--``."""
    return re.sub(r'[\\/:*?"<>|]', "--", name)


def _parse_iso(iso_str: str | None) -> float:
    if not iso_str:
        return 0.0
    from datetime import datetime
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.timestamp()
    except Exception:
        return 0.0


def _extract_cwd_from_text(text: str) -> str | None:
    if not text:
        return None
    # Match patterns like:
    # - Project directory (Coding Mode — operate here): /path/to/project
    # - Working directory: /path/to/project
    match = re.search(r"-\s*(?:Project directory|Working directory|Agent workspace)\s*\([^)]*\)?:\s*([^\n]+)", text)
    if match:
        return match.group(1).strip()
    match = re.search(r"-\s*(?:Project directory|Working directory|Agent workspace):\s*([^\n]+)", text)
    if match:
        return match.group(1).strip()
    return None


def _parse_messages(memory_content: list) -> list[Message]:
    messages = []
    for step in memory_content:
        # step is a list: [message_dict, extra]
        if not isinstance(step, list) or len(step) == 0:
            continue
        msg_dict = step[0]
        if not isinstance(msg_dict, dict):
            continue
            
        msg_id = msg_dict.get("id", "")
        role = msg_dict.get("role", "")
        sender_name = msg_dict.get("name", "")
        raw_content = msg_dict.get("content", "")
        timestamp_str = msg_dict.get("timestamp", "")
        
        # Parse timestamp
        timestamp = 0.0
        if timestamp_str:
            try:
                from datetime import datetime
                if "Z" in timestamp_str or "+" in timestamp_str:
                    dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                else:
                    dt = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S.%f")
                timestamp = dt.timestamp()
            except Exception:
                try:
                    dt = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
                    timestamp = dt.timestamp()
                except Exception:
                    pass
                    
        # Flatten content blocks
        text_parts = []
        reasoning = None
        tool_name = None
        
        if isinstance(raw_content, str):
            text_parts.append(raw_content)
        elif isinstance(raw_content, list):
            for block in raw_content:
                if isinstance(block, dict):
                    btype = block.get("type", "text")
                    if btype == "text":
                        text_parts.append(block.get("text", ""))
                    elif btype == "thinking":
                        reasoning = block.get("thinking", "")
                    elif btype == "tool_use":
                        tool_name = block.get("name", "")
                        input_args = block.get("input", "")
                        text_parts.append(f"[Tool Use: {tool_name}({input_args})]")
                    elif btype == "tool_result":
                        tool_name = block.get("name", "")
                        output = block.get("output", "")
                        text_parts.append(f"[Tool Result: {tool_name} -> {output}]")
                    elif btype in ("image", "audio", "video", "file"):
                        filename = block.get("filename", "")
                        source = block.get("source", "")
                        text_parts.append(f"[Media: {btype} {filename or source}]")
                elif isinstance(block, str):
                    text_parts.append(block)
                    
        content = "\n".join(p for p in text_parts if p).strip()
        
        messages.append(
            Message(
                msg_id=msg_id,
                role=role,
                content=content,
                timestamp=timestamp,
                tool_name=tool_name,
                reasoning=reasoning,
            )
        )
    return messages


class QwenPawAdapter(BaseAdapter):
    tool_name = "qwenpaw"

    def __init__(self):
        pass

    def _get_workspaces(self) -> list[Path]:
        workspaces = []
        # Check both qwenpaw and copaw env variables and directories cross-compatibly
        tools_to_check = [self.tool_name]
        other_tool = "copaw" if self.tool_name == "qwenpaw" else "qwenpaw"
        tools_to_check.append(other_tool)
        
        for t_name in tools_to_check:
            # 1. Check workspace env var
            env_ws = os.environ.get(f"{t_name.upper()}_WORKSPACE_DIR")
            if env_ws:
                p = Path(env_ws).expanduser().resolve()
                if p.is_dir():
                    workspaces.append(p)
                    
            # 2. Check working directory env var
            env_working = os.environ.get(f"{t_name.upper()}_WORKING_DIR")
            if env_working:
                ws_dir = Path(env_working).expanduser().resolve() / "workspaces"
                if ws_dir.is_dir():
                    workspaces.extend(d for d in ws_dir.iterdir() if d.is_dir())
                    
            # 3. Check default path
            default_working = Path(f"~/.{t_name}").expanduser().resolve()
            ws_dir = default_working / "workspaces"
            if ws_dir.is_dir():
                workspaces.extend(d for d in ws_dir.iterdir() if d.is_dir())
                
        # Deduplicate paths
        seen = set()
        deduped = []
        for w in workspaces:
            try:
                resolved = w.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    deduped.append(resolved)
            except Exception:
                if w not in seen:
                    seen.add(w)
                    deduped.append(w)
        return deduped

    def is_available(self) -> bool:
        return len(self._get_workspaces()) > 0

    def _get_session_files(self, workspace_path: Path, chat: dict) -> list[Path]:
        session_id = chat.get("session_id", "")
        user_id = chat.get("user_id", "")
        channel = chat.get("channel", "")
        
        safe_sid = sanitize_filename(session_id)
        safe_uid = sanitize_filename(user_id) if user_id else ""
        if safe_uid and safe_uid == safe_sid:
            safe_uid = ""
            
        filename = f"{safe_uid}_{safe_sid}.json" if safe_uid else f"{safe_sid}.json"
        
        paths = []
        # Check root sessions directory (old location)
        root_target = workspace_path / "sessions" / filename
        if root_target.exists():
            paths.append(root_target)
            
        # Check channel-specific directory (new location)
        if channel:
            safe_channel = sanitize_filename(channel)
            target = workspace_path / "sessions" / safe_channel / filename
            if target.exists() and target != root_target:
                paths.append(target)
                
        return paths

    def _read_messages_from_files(self, files: list[Path]) -> list[Message]:
        all_messages = []
        seen_ids = set()
        for session_file in files:
            try:
                with open(session_file, "r", encoding="utf-8", errors="surrogatepass") as sf:
                    session_data = json.load(sf)
                memory_content = session_data.get("agent", {}).get("memory", {}).get("content", [])
                messages = _parse_messages(memory_content)
                for msg in messages:
                    if msg.msg_id:
                        if msg.msg_id in seen_ids:
                            continue
                        seen_ids.add(msg.msg_id)
                    all_messages.append(msg)
            except Exception:
                continue
                
        all_messages.sort(key=lambda m: m.timestamp)
        return all_messages

    def _get_save_path(self, workspace_path: Path, chat: dict) -> Path:
        files = self._get_session_files(workspace_path, chat)
        if files:
            return files[-1]
        session_id = chat.get("session_id", "")
        user_id = chat.get("user_id", "")
        safe_sid = sanitize_filename(session_id)
        safe_uid = sanitize_filename(user_id) if user_id else ""
        if safe_uid and safe_uid == safe_sid:
            safe_uid = ""
        filename = f"{safe_uid}_{safe_sid}.json" if safe_uid else f"{safe_sid}.json"
        return workspace_path / "sessions" / filename

    def _find_chat_and_workspace(self, chat_id: str) -> tuple[Path, dict] | tuple[None, None]:
        for ws in self._get_workspaces():
            chats_file = ws / "chats.json"
            if not chats_file.exists():
                continue
            try:
                with open(chats_file, "r", encoding="utf-8") as f:
                    chats_data = json.load(f)
                for chat in chats_data.get("chats", []):
                    if chat.get("id") == chat_id:
                        return ws, chat
            except Exception:
                continue
        return None, None

    def list_sessions(
        self,
        *,
        limit: int = 50,
        since: float | None = None,
        cwd: str | None = None,
    ) -> list[SessionMeta]:
        metas = []
        for ws in self._get_workspaces():
            chats_file = ws / "chats.json"
            if not chats_file.exists():
                continue
            try:
                with open(chats_file, "r", encoding="utf-8") as f:
                    chats_data = json.load(f)
                for chat in chats_data.get("chats", []):
                    created_at = _parse_iso(chat.get("created_at"))
                    updated_at = _parse_iso(chat.get("updated_at"))
                    
                    if since and updated_at < since:
                        continue
                        
                    session_files = self._get_session_files(ws, chat)
                    if not session_files:
                        continue
                        
                    detected_cwd = None
                    message_count = 0
                    model = None
                    try:
                        messages = self._read_messages_from_files(session_files)
                        message_count = len(messages)
                        
                        for msg in messages:
                            detected_cwd = _extract_cwd_from_text(msg.content)
                            if detected_cwd:
                                break
                                
                        for sf in reversed(session_files):
                            try:
                                with open(sf, "r", encoding="utf-8", errors="surrogatepass") as f:
                                    session_data = json.load(f)
                                model = session_data.get("agent", {}).get("config", {}).get("model")
                                if model:
                                    break
                            except Exception:
                                pass
                    except Exception:
                        pass
                        
                    if not detected_cwd:
                        detected_cwd = str(ws)
                        
                    if cwd and not detected_cwd.startswith(cwd):
                        continue
                        
                    meta = SessionMeta(
                        tool=self.tool_name,
                        session_id=chat.get("id"),
                        title=chat.get("name", ""),
                        source=chat.get("channel", "cli"),
                        cwd=detected_cwd,
                        created_at=created_at,
                        updated_at=updated_at,
                        message_count=message_count,
                        model=model,
                    )
                    metas.append(meta)
            except Exception:
                continue
                
        metas.sort(key=lambda m: m.updated_at, reverse=True)
        return metas[:limit]

    def search_sessions(self, query: str, *, limit: int = 20) -> list[tuple[SessionMeta, str]]:
        q_lower = query.lower()
        results = []
        
        for ws in self._get_workspaces():
            chats_file = ws / "chats.json"
            if not chats_file.exists():
                continue
            try:
                with open(chats_file, "r", encoding="utf-8") as f:
                    chats_data = json.load(f)
                for chat in chats_data.get("chats", []):
                    session_files = self._get_session_files(ws, chat)
                    if not session_files:
                        continue
                        
                    title = chat.get("name", "")
                    title_match = q_lower in title.lower()
                    
                    try:
                        messages = self._read_messages_from_files(session_files)
                        message_count = len(messages)
                    except Exception:
                        continue
                        
                    detected_cwd = None
                    snippet = ""
                    content_match = False
                    
                    for msg in messages:
                        if not detected_cwd:
                            detected_cwd = _extract_cwd_from_text(msg.content)
                            
                        if q_lower in msg.content.lower() and not content_match:
                            content_match = True
                            snippet = msg.content
                            
                    if not detected_cwd:
                        detected_cwd = str(ws)
                        
                    if title_match or content_match:
                        created_at = _parse_iso(chat.get("created_at"))
                        updated_at = _parse_iso(chat.get("updated_at"))
                        
                        model = None
                        for sf in reversed(session_files):
                            try:
                                with open(sf, "r", encoding="utf-8", errors="surrogatepass") as f:
                                    session_data = json.load(f)
                                model = session_data.get("agent", {}).get("config", {}).get("model")
                                if model:
                                    break
                            except Exception:
                                pass
                                
                        meta = SessionMeta(
                            tool=self.tool_name,
                            session_id=chat.get("id"),
                            title=title,
                            source=chat.get("channel", "cli"),
                            cwd=detected_cwd,
                            created_at=created_at,
                            updated_at=updated_at,
                            message_count=message_count,
                            model=model,
                        )
                        
                        if not snippet:
                            snippet = title
                            
                        results.append((meta, snippet))
                        if len(results) >= limit:
                            break
            except Exception:
                continue
                
        results.sort(key=lambda r: r[0].updated_at, reverse=True)
        return results[:limit]

    def get_session(self, session_id: str) -> SessionMeta | None:
        ws, chat = self._find_chat_and_workspace(session_id)
        if not ws or not chat:
            return None
            
        session_files = self._get_session_files(ws, chat)
        if not session_files:
            return None
            
        detected_cwd = None
        message_count = 0
        model = None
        try:
            messages = self._read_messages_from_files(session_files)
            message_count = len(messages)
            
            for msg in messages:
                detected_cwd = _extract_cwd_from_text(msg.content)
                if detected_cwd:
                    break
                    
            for sf in reversed(session_files):
                try:
                    with open(sf, "r", encoding="utf-8", errors="surrogatepass") as f:
                        session_data = json.load(f)
                    model = session_data.get("agent", {}).get("config", {}).get("model")
                    if model:
                        break
                except Exception:
                    pass
        except Exception:
            pass
            
        if not detected_cwd:
            detected_cwd = str(ws)
            
        return SessionMeta(
            tool=self.tool_name,
            session_id=chat.get("id"),
            title=chat.get("name", ""),
            source=chat.get("channel", "cli"),
            cwd=detected_cwd,
            created_at=_parse_iso(chat.get("created_at")),
            updated_at=_parse_iso(chat.get("updated_at")),
            message_count=message_count,
            model=model,
        )

    def read_messages(
        self,
        session_id: str,
        *,
        window: int | None = None,
        around_msg_id: str | None = None,
    ) -> list[Message]:
        ws, chat = self._find_chat_and_workspace(session_id)
        if not ws or not chat:
            return []
            
        session_files = self._get_session_files(ws, chat)
        if not session_files:
            return []
            
        try:
            messages = self._read_messages_from_files(session_files)
        except Exception:
            return []
            
        if around_msg_id and messages:
            for i, msg in enumerate(messages):
                if msg.msg_id == around_msg_id:
                    start = max(0, i - (window or 5))
                    end = min(len(messages), i + (window or 5) + 1)
                    return messages[start:end]
        elif window:
            return messages[-window:]
            
        return messages


class CopawAdapter(QwenPawAdapter):
    tool_name = "copaw"
