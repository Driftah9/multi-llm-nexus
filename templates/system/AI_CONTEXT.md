# System Context — [SYSTEM_NAME]

> This file describes what this system IS and its current operating environment.
> The orchestrator reads this at startup. All agents and projects inherit this
> context unless a project-level AI_CONTEXT.md provides a narrower scope.
>
> Update this file when the system configuration changes.

---

## What This System Is

**[SYSTEM_NAME]** is a self-hosted, LLM-agnostic AI agent platform.

- **Orchestrator:** [ORCHESTRATOR_NAME]
- **Primary Provider:** [PRIMARY_PROVIDER]
- **Installed:** [INSTALL_DATE]
- **Host:** [HOSTNAME]
- **System User:** [USERNAME]

## Environment

```
/home/[USERNAME]/
├── SOUL.md                     ← system identity
├── IDENTITY.md                 ← orchestrator role
├── OPERATING_PROCEDURES.md     ← system behavioral rules
├── AI_CONTEXT.md               ← this file
├── [PROVIDER].md               ← AI provider shim(s)
├── Agents/                     ← dynamic system agents
└── workspace/                  ← projects and categories
```

## Active Providers

[PROVIDER_LIST]

## Active Adapters

[ADAPTER_LIST]

## Workspace

`workspace/` is the operational root. Categories and projects are created
through use — not declared at install time. The workspace grows to fit
the work being done, not the other way around.

## System Folders

| Folder | Purpose |
|--------|---------|
| Inbox/ | Network drop — large files, temporary uploads, documents |
| Logs/ | System logs only |
| Scripts/ | Operational automation — cron jobs, monitors, utilities |
| backups/ | System snapshots and rollback points |
| src/ | Core source at system level (if any) |
| tests/ | System-level test suites |
| Data/ | Persistent data — state, cache, registries |
| skills/ | Skill definitions (SKILL.md files) |
| Config/ | System configuration files |
| dockers/ | Docker stacks and compose files |
| adapters/ | Communication adapters (Mattermost, Discord, Telegram) |
| Agents/ | Dynamic system agents — empty at install, grows through use |
| Temp/ | Temporary work — screenshots, drafts, in-progress fetches |
| research_cache/ | Cached research data with TTL cleanup |
| Tools/ | External tools (obsidian-vault, flutter-sdk, etc.) |
| workspace/ | Operational root — projects and categories |
