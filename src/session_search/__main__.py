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
from collections import Counter
from datetime import datetime
from pathlib import Path

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


def _with_timeout(fn, adapter_name: str, *args, **kwargs):
    """Run fn with a 15s SIGALRM timeout. Returns (result, timed_out)."""
    import signal

    def _timeout_handler(signum, frame):
        raise TimeoutError()

    try:
        old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(15)
        try:
            return fn(*args, **kwargs), False
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)
    except TimeoutError:
        print(f"Warning: '{adapter_name}' timed out, skipping", file=sys.stderr)
        return [], True


def _project_name(cwd: str | None) -> str:
    """Extract a short project name from a cwd path."""
    if not cwd:
        return "(unknown)"
    p = Path(cwd)
    # Use the last meaningful segment
    if p.name:
        return str(p)
    return cwd


def _matches_project(cwd: str | None, project: str) -> bool:
    """Check if a session's cwd matches the project filter.

    Matches if the project string appears anywhere in the cwd path
    (case-insensitive), or if cwd ends with the project string.
    """
    if not cwd:
        return False
    return project.lower() in cwd.lower()


def cmd_projects(args):
    """List all unique project directories with session counts."""
    adapters = get_adapters(args.tool)
    project_counter: Counter = Counter()  # {cwd: total_count}

    for name, adapter in adapters:
        sessions, _ = _with_timeout(
            adapter.list_sessions, name, limit=9999
        )
        for s in sessions:
            if s.cwd:
                project_counter[s.cwd] += 1

    if not project_counter:
        print("No project data found.")
        return 0

    # Sort by session count descending
    sorted_projects = project_counter.most_common()

    # Apply --limit
    if args.limit:
        sorted_projects = sorted_projects[: args.limit]

    print(f"Found {len(project_counter)} unique projects:\n")
    print(f"{'SESSIONS':>8}  PROJECT PATH")
    print("-" * 100)
    for cwd, count in sorted_projects:
        print(f"{count:>8}  {cwd}")

    print(f"\nUse --project <path> with list/search to filter by project.")
    return 0


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

    for name, adapter in adapters:
        sessions, _ = _with_timeout(
            adapter.list_sessions, name,
            limit=args.limit, since=since, cwd=args.cwd
        )
        all_sessions.extend(sessions)

    # Filter by project
    if args.project:
        all_sessions = [s for s in all_sessions if _matches_project(s.cwd, args.project)]

    all_sessions.sort(key=lambda m: m.updated_at, reverse=True)
    all_sessions = all_sessions[: args.limit]

    if not all_sessions:
        print("No sessions found.")
        return 0

    print(f"Found {len(all_sessions)} sessions:\n")
    if args.project:
        print(f"  (filtered by project: '{args.project}')\n")
    print(f"{'TOOL':<14} {'ID':<42} {'UPDATED':<17} {'MSG':>4}  {'PROJECT':<30} TITLE")
    print("-" * 140)
    for s in all_sessions:
        title = s.title[:40] + "..." if len(s.title) > 40 else s.title
        sid = s.session_id[:40]
        proj = _project_name(s.cwd)[:28] if s.cwd else "-"
        print(f"{s.tool:<14} {sid:<42} {_fmt_time(s.updated_at):<17} {s.message_count:>4}  {proj:<30} {title}")

    print(f"\nReference format: @session:<tool>:<session_id>")
    return 0


def cmd_search(args):
    adapters = get_adapters(args.tool)
    all_results: list[tuple] = []

    for name, adapter in adapters:
        results, _ = _with_timeout(
            adapter.search_sessions, name, args.query, limit=args.limit
        )
        all_results.extend(results)

    # Filter by project
    if args.project:
        all_results = [
            (meta, snippet) for meta, snippet in all_results
            if _matches_project(meta.cwd, args.project)
        ]

    all_results.sort(key=lambda r: r[0].updated_at, reverse=True)
    all_results = all_results[: args.limit]

    if not all_results:
        print(f'No sessions found for "{args.query}".')
        return 0

    print(f'Found {len(all_results)} sessions matching "{args.query}":\n')
    if args.project:
        print(f"  (filtered by project: '{args.project}')\n")
    for i, (meta, snippet) in enumerate(all_results, 1):
        snippet = snippet.replace("\n", " ").strip()
        if len(snippet) > 120:
            snippet = snippet[:120] + "..."
        proj = _project_name(meta.cwd)[:40] if meta.cwd else ""
        print(f"  {i}. [{meta.tool}] {meta.title[:60]}")
        print(f"     ref: {meta.ref}")
        if proj:
            print(f"     project: {proj}")
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
        prog="session-search",
        description="Cross-tool session search and retrieval",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # list
    p_list = sub.add_parser("list", help="List sessions")
    p_list.add_argument("--tool", "-t", help="Comma-separated tool names (default: all)")
    p_list.add_argument("--limit", "-n", type=int, default=20)
    p_list.add_argument("--since", help="Only sessions since date (YYYY-MM-DD)")
    p_list.add_argument("--cwd", help="Filter by working directory prefix (exact match)")
    p_list.add_argument("--project", "-p", help="Filter by project name/path (fuzzy match)")
    p_list.set_defaults(func=cmd_list)

    # search
    p_search = sub.add_parser("search", help="Search sessions by keyword")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--tool", "-t", help="Comma-separated tool names (default: all)")
    p_search.add_argument("--limit", "-n", type=int, default=20)
    p_search.add_argument("--project", "-p", help="Filter by project name/path (fuzzy match)")
    p_search.set_defaults(func=cmd_search)

    # show
    p_show = sub.add_parser("show", help="Show a session's messages")
    p_show.add_argument("ref", help="Session reference: TOOL:SESSION_ID")
    p_show.add_argument("--window", "-w", type=int, help="Only show last N messages")
    p_show.add_argument("--max-chars", type=int, default=2000, help="Max chars per message")
    p_show.set_defaults(func=cmd_show)

    # projects
    p_projects = sub.add_parser("projects", help="List all projects with session counts")
    p_projects.add_argument("--tool", "-t", help="Comma-separated tool names (default: all)")
    p_projects.add_argument("--limit", "-n", type=int, default=50, help="Max projects to show")
    p_projects.set_defaults(func=cmd_projects)

    # tools
    p_tools = sub.add_parser("tools", help="List available adapters")
    p_tools.set_defaults(func=cmd_tools)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
