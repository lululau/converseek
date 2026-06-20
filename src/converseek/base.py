"""Abstract base class for all session adapters."""
from __future__ import annotations

import abc
from collections.abc import Iterator
from dataclasses import dataclass, field


@dataclass
class SessionMeta:
    """Unified session metadata across all tools."""

    tool: str  # "claude", "cursor", "hermes", etc.
    session_id: str  # tool-native session ID
    title: str = ""
    source: str = ""  # platform / channel / CLI
    cwd: str | None = None
    created_at: float = 0.0  # Unix timestamp (seconds)
    updated_at: float = 0.0
    message_count: int = 0
    model: str | None = None
    git_branch: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def ref(self) -> str:
        """Cross-tool reference handle, e.g. 'claude:abc123'."""
        return f"{self.tool}:{self.session_id}"


@dataclass
class Message:
    """Unified message representation."""

    msg_id: str = ""
    role: str = ""  # "user", "assistant", "system", "tool"
    content: str = ""
    timestamp: float = 0.0
    tool_name: str | None = None
    reasoning: str | None = None
    model: str | None = None


class BaseAdapter(abc.ABC):
    """Base class for tool-specific session adapters."""

    tool_name: str = ""

    @abc.abstractmethod
    def list_sessions(
        self,
        *,
        limit: int = 50,
        since: float | None = None,
        cwd: str | None = None,
    ) -> list[SessionMeta]:
        """List sessions, most recent first."""
        ...

    @abc.abstractmethod
    def search_sessions(self, query: str, *, limit: int = 20) -> list[tuple[SessionMeta, str]]:
        """Search sessions by keyword. Returns list of (meta, snippet) tuples."""
        ...

    @abc.abstractmethod
    def get_session(self, session_id: str) -> SessionMeta | None:
        """Get metadata for a single session."""
        ...

    @abc.abstractmethod
    def read_messages(
        self,
        session_id: str,
        *,
        window: int | None = None,
        around_msg_id: str | None = None,
    ) -> list[Message]:
        """Read messages from a session. If window/around_msg_id given, return a slice."""
        ...

    def is_available(self) -> bool:
        """Check if this tool's data exists on this machine."""
        return True
