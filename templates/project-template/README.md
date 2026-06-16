# Project: [NAME]

**Category:** [CATEGORY]  
**Created:** [TIMESTAMP]  
**Orchestrator:** [NEXUS_AGENT_NAME]

## Overview

[Add project description here]

## Status

- [ ] Scoped
- [ ] In progress
- [ ] Complete

## Structure

```
[NAME]/
├── AI_CONTEXT.md      ← project context (single source of truth — edit this)
├── CLAUDE.md          ← AI shim (Claude Code)
├── .cursorrules       ← AI shim (Cursor)
├── .windsurfrules     ← AI shim (Windsurf)
├── .github/
│   └── copilot-instructions.md  ← AI shim (GitHub Copilot)
├── .nexus/            ← Nexus metadata
└── work/
    ├── src/           ← source code / implementation
    ├── docs/          ← documentation, design decisions, research
    ├── scripts/       ← automation and utilities
    └── archive/       ← completed or deprecated work
```

All working files go under `work/`. The project root is for context and config only.
To switch AI providers, add a new shim at the root pointing to `AI_CONTEXT.md` — no other changes needed.

## Linked Channels

See `.nexus/links.yaml` for adapter channel mappings.

## Commands

Link to a communication channel:
```
@bot link [project-name] to mattermost:[channel-name]
@bot link [project-name] to discord:[channel-id]
@bot link [project-name] to telegram:[topic-id]
```

View project status:
```
@bot status [project-name]
@bot recent [project-name]
```
