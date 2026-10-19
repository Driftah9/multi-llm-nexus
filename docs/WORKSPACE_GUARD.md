# Workspace Guard — the office model

**Date:** 2026-10-19 · **Status:** Built and tested (42 tests) · **Source:** live claude-brain `node_map_guard.py`
**Code:** `src/core/workspace_guard.py`, `scripts/workspace_guard_hook.py` · `# NEXUS:PORTABLE`

## The model

The operator's `workspace/` (resolved via `layout.path("workspace")`, never hardcoded) holds one
directory per project. A context — channel, topic, room — that the operator **links** to a project
works inside that project's top-level folder: its **office**.

| Where | Allowed | Needs operator confirmation |
|---|---|---|
| Inside the office | add / edit / delete anything — "empty the project" clears all of it | the office root; scaffolding + operational entries **themselves** (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `README.md`, `src/`, `docs/`, `.git/`, `.claude/`, `.env`, `.workspace-tools.yml`, plus a project manifest's `protected_paths`) — their *contents* are workable |
| Nested projects (`workspace/<X>/<sub>/…`) | sub-rooms of X's office — a context linked anywhere inside X works across all of X | — |
| Another project, from a linked context | — | everything destructive (contained until the operator says otherwise) |
| Unlinked context (general chat, DMs, CLI) | works across projects | root + scaffolding everywhere |

Scope is deliberately narrow:
- Only **destructive** operations are gated: `rm`/`rmdir`/`shred`/`dd`/`mkfs`/`truncate`,
  `find -delete`, `xargs rm`, and overwrite redirects (`> file`). `mv` only warns.
  Editing scaffolding is allowed — those files are meant to be kept current.
- **Reading is never blocked.** Staying out of other projects while linked is a behavioural rule
  (projects overlap; crossing over is the operator's call), not a sandbox.

## How a command is read

Only the operands of the destructive segment count. `cd <project> && rm x` targets `x`, not the
project root (the live guard originally read every path in the command and blocked this). Parsing
is quote-aware (`grep -vE 'a|b'` is not a pipe), tracks `cd`, expands globs (`rm -rf *` sees every
scaffold entry it would hit), and follows `find … | xargs rm`.

Known limit: names produced at runtime (`ls | xargs rm -rf` with no filter) cannot be seen
statically. The guard is a seatbelt, not a sandbox.

## Wiring

**In-process (any provider through the agent loop):**

```python
from src.core.workspace_guard import WorkspaceGuard, guard_tool_call

guard = WorkspaceGuard()                       # root from the layout manifest
async def guard_fn(name, args):
    v = guard_tool_call(name, args, home=linked_project_dir, guard=guard)   # None = unlinked
    return v.allowed, v.reason

await run_agent(provider, prompt, registry=reg, guard_fn=guard_fn)
```

`guard_fn` runs on **every** tool call, before the `requires_confirmation` gate; a block is fed back
to the model as a `BLOCKED:` tool result, and a guard that raises blocks that call (fail-closed).
`guard_tool_call` maps shell-style tools (a `command` argument) and delete-style tools
(`delete*/remove*/unlink*` with `path`/`file_path`).

**`claude_code` provider (Claude Code CLI):** register the hook, matcher `Bash`:

```json
{"type": "command", "command": "python3 <repo>/scripts/workspace_guard_hook.py"}
```

The linked project is taken from the turn's `cwd`. `NEXUS_WORKSPACE_ROOT` overrides the manifest
root. The hook loads its two modules by file path (~0.1 s) instead of importing the `src.core`
package (~6 s engine boot), because it runs on every shell call.

Last verified against code: 2026-10-19
