---
name: converseek
description: "Search, browse, and export sessions across 9 AI coding tools: Claude Code, Cursor, Antigravity 2.0, OpenCode, ZCode, Paseo, Hermes, QwenPaw, and Copaw."
version: 1.0.0
author: Hermes Agent
metadata:
  hermes:
    tags: [session, search, cross-tool, reference, claude, cursor, hermes, opencode, qwenpaw, copaw]
    related_skills: [electron-app-investigation]
---

# Session Search

Cross-tool session search, browse, and export. Query and read conversations from **9 AI coding tools** through a single CLI.

## Supported Tools

| Tool                | Data Source                                                    | Session Count |
|---------------------|----------------------------------------------------------------|---------------|
| **Claude Code**     | `~/.claude/projects/` (JSONL + index)                          | ~394          |
| **Cursor**          | `~/Library/Application Support/Cursor/.../state.vscdb`         | ~1,264        |
| **Antigravity 2.0** | `~/.gemini/antigravity/conversations/*.db` (SQLite + Protobuf) | ~55           |
| **OpenCode**        | `~/.local/share/opencode/opencode.db`                          | ~858          |
| **ZCode**           | `~/.zcode/cli/db/db.sqlite`                                    | varies        |
| **Paseo**           | `~/.paseo/agents/` (JSON files)                                | varies        |
| **Hermes**          | `~/.hermes/state.db` (SQLite + FTS5)                           | varies        |
| **QwenPaw**         | `~/.qwenpaw/workspaces/` (JSON files)                          | varies        |
| **Copaw**           | `~/.copaw/workspaces/` (JSON files)                            | varies        |

## Quick Start

```bash
# List available tools and their status
uvx converseek tools

# List recent sessions across all tools
uvx converseek list --limit 10

# List from a specific tool
uvx converseek list --tool hermes --limit 5

# Filter by project
uvx converseek list --project myapp

# Search across all tools
uvx converseek search "docker networking"

# Search specific tools only
uvx converseek search "auth refactor" --tool claude,hermes

# Search within a project
uvx converseek search "auth" --project myapp

# Read a session's messages
uvx converseek show hermes:20260620_201309_a8e8cb95
uvx converseek show claude:f2f188c7-... --window 20

# Export a session to Markdown
uvx converseek export hermes:20260620_201309_a8e8cb95
uvx converseek export hermes:20260620_201309_a8e8cb95 -o session.md

# List all projects with session counts
uvx converseek projects
```

## Commands

### `tools`
List all adapters and their availability status.

### `list [options]`
List sessions, most recent first.

| Option           | Description                                 |
|------------------|---------------------------------------------|
| `--tool TOOL`    | Comma-separated tool names (default: all)   |
| `--limit N`      | Max sessions to show (default: 20)          |
| `--since DATE`   | Only sessions since date (YYYY-MM-DD)       |
| `--cwd PATH`     | Filter by working directory prefix          |
| `--project PATH` | Filter by project name/path (fuzzy match)   |

### `search QUERY [options]`
Full-text search across sessions. Searches titles and message content.

| Option           | Description                                 |
|------------------|---------------------------------------------|
| `--tool TOOL`    | Comma-separated tool names (default: all)   |
| `--limit N`      | Max results (default: 20)                   |
| `--project PATH` | Filter by project name/path (fuzzy match)   |

Per-adapter timeout: 15 seconds. Slow tools (Antigravity) are skipped if they timeout.

### `show TOOL:SESSION_ID [options]`
Display messages from a specific session.

| Option          | Description                           |
|-----------------|---------------------------------------|
| `--window N`    | Only show last N messages             |
| `--max-chars N` | Max chars per message (default: 2000) |

### `export TOOL:SESSION_ID [options]`
Export a session to a Markdown file with metadata and full message history.

| Option       | Description                                                  |
|--------------|--------------------------------------------------------------|
| `-o FILE`    | Output file path (default: `./<tool>-<session_id>.md`)       |

### `projects [options]`
List all unique project directories with session counts.

| Option        | Description                               |
|---------------|-------------------------------------------|
| `--tool TOOL` | Comma-separated tool names (default: all) |
| `--limit N`   | Max projects to show (default: 50)        |

## Cross-Tool Reference Format

All sessions use a unified reference format:

```
<tool>:<session_id>
```

Examples:
- `hermes:20260620_201309_a8e8cb95`
- `claude:f2f188c7-77cf-45c7-bc2f-fe26ed61beb4`
- `cursor:b6d91996-70c7-426c-9423-337b96a1c3c1`
- `antigravity:00d476a2-b9cb-40cc-937d-a7819a750de2`
- `opencode:ses_1981d72b6ffeN5bTHtWW7EsSZn`
- `zcode:sess_5b9d6024-78c6-4835-b4e8-6ea182630c9a`
- `paseo:9f34605d-12fd-4a28-ba02-dc9d6d7049e8`
- `qwenpaw:925ff85e-0baa-4a8b-8bf5-8c1d6d55ca36`
- `copaw:925ff85e-0baa-4a8b-8bf5-8c1d6d55ca36`

## Architecture

Each tool has a dedicated adapter implementing three operations:
- `list_sessions()` — enumerate session metadata
- `search_sessions()` — keyword search within sessions
- `read_messages()` — retrieve full or windowed message history

```
converseek/
│
├── base.py                 # Abstract interface + data models
└── adapters/
    ├── claude_code.py      # JSONL + sessions-index.json
    ├── hermes.py           # state.db with FTS5
    ├── opencode.py         # opencode.db (message → part schema)
    ├── zcode.py            # db.sqlite (same message → part schema)
    ├── paseo.py            # JSON files (metadata only, no transcripts)
    ├── cursor.py           # state.vscdb (composerData + bubbleId)
    ├── antigravity.py      # .db + protoc --decode_raw
    └── qwenpaw.py          # JSON files (chats.json and sessions/, handles copaw)
```

## References

- `references/tool-session-formats.md` — Detailed data format reference for all tools' session storage (SQLite schemas, JSONL structures, protobuf field mappings, key formats)

## Pitfalls

- **Antigravity is slow**: Each session DB requires `protoc --decode_raw` subprocess calls. The `list` command skips payload decoding (only reads file mtime + metadata blob). The `search` command uses hex matching on raw protobuf bytes to avoid full decoding.
- **Cursor composerData format**: Conversation headers are in `fullConversationHeadersOnly` field. Actual text is in separate `bubbleId:<composerId>:<bubbleId>` entries. Empty sessions and `task-*` stubs are filtered out.
- **OpenCode dual schema**: New versions use `~/.local/share/opencode/opencode.db` with `session` + `message` + `part` tables. Legacy uses `~/.opencode/opencode.db` with `sessions` + `messages`. The adapter auto-detects.
- **ZCode content in parts**: Like OpenCode, ZCode stores message text in the `part` table, not in `message.data`. The `message.data` only has metadata (role, model, timestamps).
- **Paseo metadata only**: Paseo agent JSON files contain session metadata but not full transcripts. `read_messages()` returns empty for Paseo.
- **QwenPaw / Copaw session merging**: Session history might be split between the root `sessions/` directory (old format) and channel-specific `sessions/{channel}/` directory (new format). The adapter automatically scans, merges, and deduplicates messages across both locations to construct the full history.
- **Per-adapter timeout**: List and search operations have a 15-second timeout per adapter to prevent slow tools from blocking the entire query.
