# converseek

Cross-tool session search, browse, and export for AI coding tools.

## Supported Tools

| Tool            | Data Source                                                    |
|-----------------|----------------------------------------------------------------|
| Claude Code     | `~/.claude/projects/` (JSONL + index)                          |
| Cursor          | `state.vscdb` (composerData + bubbleId)                        |
| Antigravity 2.0 | `~/.gemini/antigravity/conversations/*.db` (SQLite + Protobuf) |
| OpenCode        | `~/.local/share/opencode/opencode.db`                          |
| ZCode           | `~/.zcode/cli/db/db.sqlite`                                    |
| Paseo           | `~/.paseo/agents/` (JSON)                                      |
| Hermes          | `~/.hermes/state.db` (SQLite + FTS5)                           |
| QwenPaw / Copaw | `~/.qwenpaw/workspaces/` / `~/.copaw/workspaces/` (JSON)       |

## Install

```bash
uv tool install converseek
```

## Usage

```bash
uvx converseek tools                                         # list adapters
uvx converseek list --limit 10                               # recent sessions
uvx converseek list --tool hermes --limit 5                  # filter by tool
uvx converseek list --project myapp                          # filter by project
uvx converseek search "docker networking"                    # full-text search
uvx converseek search "auth" --tool claude,hermes            # search specific tools
uvx converseek show hermes:20260620_201309_a8e8cb95          # read a session
uvx converseek export hermes:20260620_201309_a8e8cb95        # export to markdown
uvx converseek projects                                      # list all projects
```

## Development

### Bump Version

This project uses `bump-my-version` to manage versioning. You can bump the version number using `uvx`:

```bash
uvx bump-my-version bump patch   # e.g., 0.2.0 -> 0.2.1
uvx bump-my-version bump minor   # e.g., 0.2.0 -> 0.3.0
uvx bump-my-version bump major   # e.g., 0.2.0 -> 1.0.0
```
