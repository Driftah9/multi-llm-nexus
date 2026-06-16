# [AGENT_NAME] — [ROLE_TITLE]

> Copy this file to create a new agent. Rename it: agent_name.md or role_name.md
> Named agents: cpa_agent_susan.md — Role agents: humanresources.md
> Delete this template file after creating your first real agent.

---

## Role

**Title:** [ROLE_TITLE]
**Scope:** [What this agent is responsible for]
**Reports to:** [ORCHESTRATOR_NAME]

## Responsibilities

[List what this agent does — be specific, not general]

1.
2.
3.

## Tools and Access

[What scripts, APIs, or resources this agent can use]

- Script: `work/scripts/[script].py`
- API: [endpoint or service]
- Data: `work/[folder]/`

## Communication Style

[How this agent presents information — formal/casual, brief/detailed, format]

## Domain Rules

[Rules specific to this agent's role — what it checks, what it flags, what it refuses]

## Escalation

[When this agent stops and asks — what triggers escalation to the user or orchestrator]

---

## Context Loading Order

When this agent is called:
1. System SOUL.md
2. System OPERATING_PROCEDURES.md
3. Project SOUL.md (if present)
4. Project OPERATING_PROCEDURES.md (if present)
5. Project AI_CONTEXT.md
6. This file
7. Task context (injected at call time)
