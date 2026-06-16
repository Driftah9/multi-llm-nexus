# Project: [NAME]

> **This is the single source of truth for this project.**
> `CLAUDE.md`, `.cursorrules`, `.windsurfrules`, and `.github/copilot-instructions.md`
> are thin shims that point here. Edit this file — not the shims.

## Identity

- **Project:** [NAME]
- **Category:** [CATEGORY]
- **Orchestrator:** [NEXUS_AGENT_NAME]
- **Primary Model:** [PRIMARY_PROVIDER_MODEL]

## Overview

[Add project description here — what this project does and why it exists]

## Workspace Layout

All working files live under `work/`. The project root contains only
context/config files that AI tools and Nexus read on startup.

```
[NAME]/
├── AGENTS.md                  ← this file (single source of truth)
├── CLAUDE.md                  ← shim → AGENTS.md (Claude Code)
├── .cursorrules               ← shim → AGENTS.md (Cursor)
├── .windsurfrules             ← shim → AGENTS.md (Windsurf)
├── .github/
│   └── copilot-instructions.md  ← shim → AGENTS.md (GitHub Copilot)
├── .nexus/                    ← Nexus metadata (links, workspace config)
└── work/
    ├── src/                   ← source code / implementation
    ├── docs/                  ← documentation, design decisions, research
    ├── scripts/               ← automation, tooling, utilities
    └── archive/               ← completed or deprecated work
```

## Task Routing

| Task                  | Work in          | Notes                        |
|-----------------------|------------------|------------------------------|
| Write / edit code     | `work/src/`      |                              |
| Documentation         | `work/docs/`     |                              |
| Scripts / automation  | `work/scripts/`  |                              |
| Completed work        | `work/archive/`  | Move, don't delete           |

## Conventions

- Naming: `feature-name_draft.md`, `feature-name_v2.py`, `YYYY-MM-DD-decision.md`
- One fact lives in one place — no duplication between files
- Update this file when the project changes; don't let it go stale

## Agent Roles

### Primary Orchestrator

- **Name:** [NEXUS_AGENT_NAME]
- **Role:** Chief of Staff — coordinates project work, routes tasks to specialists

### Specialists (Optional)

```yaml
# Example: Research Specialist
name: Research
model_tier: standard
prompt: |
  You are researching [topic] for this project.
  Focus on: [what to research]
  Output format: [how to present findings]

# Example: Code Reviewer
name: Code Reviewer
model_tier: deep
prompt: |
  Review [type] code for this project.
  Check: [what to check]
  Standards: [what standards apply]
```

To add a specialist: define it above, then reference it in chat:
`@bot [specialist-name] [task]`
