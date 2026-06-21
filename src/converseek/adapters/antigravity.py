"""Antigravity 2.0 adapter.

Reads from ~/.gemini/antigravity/conversations/*.db
Each .db is one conversation:
  - trajectory_metadata_blob: conversation metadata (Protobuf)
  - steps: individual steps with step_payload (Protobuf)
  - gen_metadata: generation metadata (Protobuf)

We use protoc --decode_raw for Protobuf parsing, which is best-effort.
For text extraction, we focus on field 19 (user prompts) in step_payload.
"""
from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import tempfile
from pathlib import Path

from ..base import BaseAdapter, SessionMeta, Message


AG_DIR = Path.home() / ".gemini" / "antigravity" / "conversations"


class AntigravityAdapter(BaseAdapter):
    tool_name = "antigravity"

    def __init__(self, base_dir: Path | None = None):
        self.base_dir = base_dir or AG_DIR

    def is_available(self) -> bool:
        return self.base_dir.is_dir()

    def _decode_protobuf(self, blob: bytes) -> str:
        """Decode a Protobuf blob using protoc --decode_raw."""
        try:
            with tempfile.NamedTemporaryFile(suffix=".pb", delete=False) as tf:
                tf.write(blob)
                tf_path = tf.name
            result = subprocess.run(
                ["protoc", "--decode_raw"],
                stdin=open(tf_path, "rb"),
                capture_output=True,
                timeout=5,
            )
            os.unlink(tf_path)
            return result.stdout.decode("utf-8", errors="replace")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return ""
        except Exception:
            if os.path.exists(tf_path):
                os.unlink(tf_path)
            return ""

    def _extract_metadata(self, db_path: Path) -> dict:
        """Extract conversation metadata from trajectory_metadata_blob."""
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT data FROM trajectory_metadata_blob WHERE id = 'main'"
            ).fetchone()
            if not row:
                return {}
            decoded = self._decode_protobuf(row["data"])
            # Parse decoded text for useful fields
            info: dict = {"raw": decoded}
            for line in decoded.split("\n"):
                line = line.strip()
                # field 1.1 = workspace path (file:///...)
                if line.startswith('1: "file://'):
                    info["cwd"] = line.split('"')[1].replace("file://", "")
                # field 3 = conversation/trajectory ID
                if line.startswith("3: ") and not line.startswith("3 {"):
                    info["trajectory_id"] = line.split('"')[1] if '"' in line else ""
            return info
        finally:
            conn.close()

    def _get_step_payloads(self, db_path: Path) -> list[dict]:
        """Get all steps with decoded payloads."""
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT idx, step_type, status, step_payload FROM steps ORDER BY idx"
            ).fetchall()
            steps = []
            for row in rows:
                payload_raw = row["step_payload"]
                if not payload_raw:
                    continue
                decoded = self._decode_protobuf(payload_raw)
                steps.append({
                    "idx": row["idx"],
                    "step_type": row["step_type"],
                    "status": row["status"],
                    "decoded": decoded,
                })
            return steps
        finally:
            conn.close()

    def _extract_text_from_decoded(self, decoded: str, is_user: bool = True) -> str:
        """Extract human-readable text from decoded protobuf.

        In the step_payload protobuf:
        - Field 19 contains user prompts (subfield 2 or 3)
        - Field 20 contains assistant text responses (subfield 1)
        """
        texts = []
        brace_depth = 0
        in_field = False
        in_field_depth = 0
        target_field = "19 {" if is_user else "20 {"
        target_subfield = ("2: \"", "3: \"") if is_user else ("1: \"",)
        uuid_pattern = re.compile(
            r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
        )

        for line in decoded.split("\n"):
            stripped = line.strip()
            
            if "{" in stripped:
                opens = stripped.count("{")
                for _ in range(opens):
                    brace_depth += 1
                    if stripped.startswith(target_field) and not in_field:
                        in_field = True
                        in_field_depth = brace_depth

            if in_field and brace_depth == in_field_depth:
                if any(stripped.startswith(prefix) for prefix in target_subfield):
                    try:
                        val = stripped.split('"', 1)[1].rsplit('"', 1)[0]
                        val = _decode_octal_escapes(val)
                        if val and len(val) > 3:
                            if not uuid_pattern.match(val):
                                texts.append(val)
                    except IndexError:
                        pass

            if "}" in stripped:
                closes = stripped.count("}")
                for _ in range(closes):
                    if in_field and brace_depth == in_field_depth:
                        in_field = False
                        in_field_depth = 0
                    brace_depth -= 1

        return "\n".join(texts)

    def list_sessions(
        self,
        *,
        limit: int = 50,
        since: float | None = None,
        cwd: str | None = None,
    ) -> list[SessionMeta]:
        db_files = sorted(self.base_dir.glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
        metas = []
        for db_path in db_files:
            stat = db_path.stat()
            created = stat.st_mtime
            if since and created < since:
                continue
            meta_info = self._extract_metadata(db_path)
            meta = SessionMeta(
                tool=self.tool_name,
                session_id=db_path.stem,
                title="",
                source="antigravity",
                cwd=meta_info.get("cwd"),
                created_at=created,
                updated_at=created,
            )
            if cwd and meta.cwd and not meta.cwd.startswith(cwd):
                continue
            metas.append(meta)
            if len(metas) >= limit:
                break
        # Lazy title loading: only fetch titles for the returned sessions
        for meta in metas:
            db_path = self.base_dir / f"{meta.session_id}.db"
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            try:
                # Get first user prompt step only (step_type 7 or 14)
                row = conn.execute(
                    "SELECT step_payload FROM steps WHERE step_type IN (7, 14) ORDER BY idx LIMIT 1"
                ).fetchone()
                if row and row["step_payload"]:
                    decoded = self._decode_protobuf(row["step_payload"])
                    text = self._extract_text_from_decoded(decoded)
                    if text:
                        meta.title = text[:200]
            finally:
                conn.close()
        return metas

    def search_sessions(self, query: str, *, limit: int = 20) -> list[tuple[SessionMeta, str]]:
        q_lower = query.lower()
        # Only search in titles first (fast, no protoc calls)
        metas = self.list_sessions(limit=100)
        results = []
        for meta in metas:
            if q_lower in meta.title.lower():
                results.append((meta, meta.title[:300]))
                if len(results) >= limit:
                    return results
        # If not enough results, search step payloads (slower)
        for meta in metas:
            if meta in [r[0] for r in results]:
                continue
            db_path = self.base_dir / f"{meta.session_id}.db"
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            try:
                # Quick SQL check: does any step_payload contain the query bytes?
                rows = conn.execute(
                    "SELECT hex(step_payload) FROM steps WHERE step_type IN (7, 14) ORDER BY idx"
                ).fetchall()
            finally:
                conn.close()
            for row in rows:
                if not row[0]:
                    continue
                # Convert query to UTF-8 hex for a raw match
                query_hex = query.encode("utf-8").hex()
                if query_hex.lower() in row[0].lower():
                    blob = bytes.fromhex(row[0])
                    decoded = self._decode_protobuf(blob)
                    text = self._extract_text_from_decoded(decoded)
                    if text:
                        results.append((meta, text[:300]))
                        break
            if len(results) >= limit:
                break
        return results[:limit]

    def get_session(self, session_id: str) -> SessionMeta | None:
        db_path = self.base_dir / f"{session_id}.db"
        if not db_path.exists():
            return None
        metas = self.list_sessions(limit=9999)
        for meta in metas:
            if meta.session_id == session_id:
                return meta
        return None

    def read_messages(
        self,
        session_id: str,
        *,
        window: int | None = None,
        around_msg_id: str | None = None,
    ) -> list[Message]:
        db_path = self.base_dir / f"{session_id}.db"
        if not db_path.exists():
            return []
        steps = self._get_step_payloads(db_path)
        messages = []
        for step in steps:
            is_user = step["step_type"] in (7, 14)
            is_assistant = step["step_type"] == 15
            if not is_user and not is_assistant:
                continue
            text = self._extract_text_from_decoded(step["decoded"], is_user=is_user)
            if not text:
                continue
            role = "user" if is_user else "assistant"
            messages.append(Message(
                msg_id=str(step["idx"]),
                role=role,
                content=text,
                timestamp=0.0,
            ))
        if window:
            return messages[-window:]
        return messages


def _decode_octal_escapes(s: str) -> str:
    """Decode protobuf octal escape sequences like \\346\\210\\221."""
    result = []
    i = 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s) and s[i + 1].isdigit():
            # Octal escape: \NNN (3 octal digits)
            octal = s[i + 1 : i + 4]
            try:
                byte_val = int(octal, 8)
                result.append(byte_val)
                i += 4
            except ValueError:
                result.append(ord(s[i]))
                i += 1
        else:
            result.append(ord(s[i]))
            i += 1
    return bytes(result).decode("utf-8", errors="replace")
