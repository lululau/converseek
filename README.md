# session-search

Cross-tool session search and retrieval for 7 AI coding tools.

## Supported Tools

| Tool | Data Source |
|------|-------------|
| Claude Code | `~/.claude/projects/` (JSONL + index) |
| Cursor | `state.vscdb` (composerData + bubbleId) |
| Antigravity 2.0 | `~/.gemini/antigravity/conversations/*.db` (SQLite + Protobuf) |
| OpenCode | `~/.local/share/opencode/opencode.db` |
| ZCode | `~/.zcode/cli/db/db.sqlite` |
| Paseo | `~/.paseo/agents/` (JSON) |
| Hermes | `~/.hermes/state.db` (SQLite + FTS5) |

## Install

```bash
uv tool install .
# or
uv run session-search tools
```

## Usage

```bash
session-search tools                                    # list adapters
session-search list --limit 10                         # recent sessions
session-search list --tool hermes --limit 5            # filter by tool
session-search search "docker networking"              # full-text search
session-search search "auth" --tool claude-code,hermes # search specific tools
session-search show hermes:20260620_201309_a8e8cb95    # read a session
```
