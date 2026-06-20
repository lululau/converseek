# converseek

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
uv run converseek tools
```

## Usage

```bash
converseek tools                                    # list adapters
converseek list --limit 10                         # recent sessions
converseek list --tool hermes --limit 5            # filter by tool
converseek search "docker networking"              # full-text search
converseek search "auth" --tool claude-code,hermes # search specific tools
converseek show hermes:20260620_201309_a8e8cb95    # read a session
```
