#!/usr/bin/env python3
"""session_search — cross-tool session search and retrieval.

Search and read sessions from 7 AI coding tools:
  claude-code, hermes, opencode, paseo, zcode, cursor, antigravity

Usage:
    session_search list [--tool TOOL] [--limit N] [--since DATE] [--cwd PATH]
    session_search search QUERY [--tool TOOL] [--limit N]
    session_search show TOOL:SESSION_ID [--window N]
    session_search tools

Examples:
    session_search list --limit 10
    session_search search "docker networking"
    session_search search "auth refactor" --tool claude-code,hermes
    session_search show hermes:20260620_201309_a8e8cb95
    session_search tools
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

from .adapters.claude_code import ClaudeCodeAdapter
from .adapters.hermes import HermesAdapter
from .adapters.opencode import OpenCodeAdapter
from .adapters.paseo import PaseoAdapter
from .adapters.zcode import ZCodeAdapter
from .adapters.cursor import CursorAdapter
from .adapters.antigravity import AntigravityAdapter


ADAPTERS = {
    "claude-code": ClaudeCodeAdapter,
    "hermes": HermesAdapter,
    "opencode": OpenCodeAdapter,
    "paseo": PaseoAdapter,
    "zcode": ZCodeAdapter,
    "cursor": CursorAdapter,
    "antigravity": AntigravityAdapter,
}


def get_adapters(tools: str | None = None) -> list:
    """Get adapter instances for specified tools (or all available)."""
    if tools:
        names = [t.strip() for t in tools.split(",")]
    else:
        names = list(ADAPTERS.keys())
    instances = []
    for name in names:
        cls = ADAPTERS.get(name)
        if not cls:
            print(f"Warning: unknown tool '{name}'", file=sys.stderr)
            continue
        instance = cls()
        if instance.is_available():
            instances.append((name, instance))
        else:
            print(f"Info: '{name}' data not found, skipping", file=sys.stderr)
    return instances


def _fmt_time(ts: float) -> str:
    if not ts:
        return "?"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def cmd_list(args):
    since = None
    if args.since:
        try:
            since = datetime.fromisoformat(args.since).timestamp()
        except ValueError:
            print(f"Error: invalid date format '{args.since}', use YYYY-MM-DD", file=sys.stderr)
            return 1

    adapters = get_adapters(args.tool)
    all_sessions = []
    import signal

    def _timeout_handler(signum, frame):
        raise TimeoutError()

    for name, adapter in adapters:
        try:
            old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
            signal.alarm(15)
            try:
                sessions = adapter.list_sessions(limit=args.limit, since=since, cwd=args.cwd)
                all_sessions.extend(sessions)
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old_handler)
        except TimeoutError:
            print(f"Warning: '{name}' list timed out, skipping", file=sys.stderr)

    all_sessions.sort(key=lambda m: m.updated_at, reverse=True)
    all_sessions = all_sessions[: args.limit]

    if not all_sessions:
        print("No sessions found.")
        return 0

    print(f"Found {len(all_sessions)} sessions:\n")
    print(f"{'TOOL':<14} {'ID':<42} {'UPDATED':<17} {'MSG':>4}  TITLE")
    print("-" * 120)
    for s in all_sessions:
        title = s.title[:50] + "..." if len(s.title) > 50 else s.title
        sid = s.session_id[:40]
        print(f"{s.tool:<14} {sid:<42} {_fmt_time(s.updated_at):<17} {s.message_count:>4}  {title}")

    print(f"\nReference format: @session:<tool>:<session_id>")
    return 0


def cmd_search(args):
    adapters = get_adapters(args.tool)
    all_results: list[tuple] = []
    import signal

    def _timeout_handler(signum, frame):
        raise TimeoutError()

    for name, adapter in adapters:
        try:
            # Per-adapter timeout to prevent slow tools from blocking
            old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
            signal.alarm(15)  # 15s per adapter
            try:
                results = adapter.search_sessions(args.query, limit=args.limit)
                all_results.extend(results)
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old_handler)
        except TimeoutError:
            print(f"Warning: '{name}' search timed out, skipping", file=sys.stderr)
        except Exception as e:
            print(f"Error searching {name}: {e}", file=sys.stderr)

    all_results.sort(key=lambda r: r[0].updated_at, reverse=True)
    all_results = all_results[: args.limit]

    if not all_results:
        print(f'No sessions found for "{args.query}".')
        return 0

    print(f'Found {len(all_results)} sessions matching "{args.query}":\n')
    for i, (meta, snippet) in enumerate(all_results, 1):
        snippet = snippet.replace("\n", " ").strip()
        if len(snippet) > 120:
            snippet = snippet[:120] + "..."
        print(f"  {i}. [{meta.tool}] {meta.title[:60]}")
        print(f"     ref: {meta.ref}")
        print(f"     {snippet}")
        print()
    return 0


def cmd_show(args):
    parts = args.ref.split(":", 1)
    if len(parts) != 2:
        print(f"Error: invalid reference '{args.ref}'. Use TOOL:SESSION_ID", file=sys.stderr)
        return 1
    tool_name, session_id = parts

    cls = ADAPTERS.get(tool_name)
    if not cls:
        print(f"Error: unknown tool '{tool_name}'", file=sys.stderr)
        return 1

    adapter = cls()
    if not adapter.is_available():
        print(f"Error: '{tool_name}' data not found", file=sys.stderr)
        return 1

    meta = adapter.get_session(session_id)
    if not meta:
        print(f"Session not found: {args.ref}", file=sys.stderr)
        return 1

    print(f"Session: {meta.ref}")
    print(f"Title:   {meta.title}")
    print(f"Tool:    {meta.tool}")
    print(f"CWD:     {meta.cwd or '?'}")
    print(f"Created: {_fmt_time(meta.created_at)}")
    print(f"Updated: {_fmt_time(meta.updated_at)}")
    print(f"Model:   {meta.model or '?'}")
    print(f"Messages: {meta.message_count}")
    print()

    messages = adapter.read_messages(session_id, window=args.window)
    if not messages:
        print("(No messages found or message reading not supported for this tool)")
        return 0

    for msg in messages:
        role_label = {"user": "👤 USER", "assistant": "🤖 ASSISTANT", "system": "⚙️ SYSTEM"}
        label = role_label.get(msg.role, f"📋 {msg.role.upper()}")
        ts = _fmt_time(msg.timestamp) if msg.timestamp else ""
        print(f"{'─' * 80}")
        print(f"{label}  {ts}  {msg.msg_id[:20] if msg.msg_id else ''}")
        print(f"{'─' * 80}")
        content = msg.content
        if args.max_chars and len(content) > args.max_chars:
            content = content[: args.max_chars] + f"\n... ({len(msg.content)} chars total)"
        print(content)
        if msg.tool_name:
            print(f"\n[tool: {msg.tool_name}]")
        print()
    return 0


def cmd_tools(args):
    print("Available adapters:\n")
    print(f"{'TOOL':<16} {'STATUS':<10} {'LOCATION'}")
    print("-" * 90)
    for name, cls in ADAPTERS.items():
        instance = cls()
        status = "✅ active" if instance.is_available() else "❌ not found"
        # Show data location
        for attr in ("base_dir", "db_path"):
            if hasattr(instance, attr):
                loc = str(getattr(instance, attr))
                break
        else:
            loc = "?"
        print(f"{name:<16} {status:<10} {loc}")
    return 0


def main():
    parser = argparse.ArgumentParser(
        prog="session_search",
        description="Cross-tool session search and retrieval",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # list
    p_list = sub.add_parser("list", help="List sessions")
    p_list.add_argument("--tool", "-t", help="Comma-separated tool names (default: all)")
    p_list.add_argument("--limit", "-n", type=int, default=20)
    p_list.add_argument("--since", help="Only sessions since date (YYYY-MM-DD)")
    p_list.add_argument("--cwd", help="Filter by working directory prefix")
    p_list.set_defaults(func=cmd_list)

    # search
    p_search = sub.add_parser("search", help="Search sessions by keyword")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--tool", "-t", help="Comma-separated tool names (default: all)")
    p_search.add_argument("--limit", "-n", type=int, default=20)
    p_search.set_defaults(func=cmd_search)

    # show
    p_show = sub.add_parser("show", help="Show a session's messages")
    p_show.add_argument("ref", help="Session reference: TOOL:SESSION_ID")
    p_show.add_argument("--window", "-w", type=int, help="Only show last N messages")
    p_show.add_argument("--max-chars", type=int, default=2000, help="Max chars per message")
    p_show.set_defaults(func=cmd_show)

    # tools
    p_tools = sub.add_parser("tools", help="List available adapters")
    p_tools.set_defaults(func=cmd_tools)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
