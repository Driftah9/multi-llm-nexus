# [PROJECT_NAME] — Project Procedures

> These procedures refine the system OPERATING_PROCEDURES.md for this project.
> System rules are the floor — they always apply. Rules here layer on top.
>
> You may add stricter rules, domain-specific behaviors, and project-specific
> escalation paths. You may NOT remove or weaken system-level rules.

---

## When in Doubt

System default: ask the user.

Project refinement: [Fill in — who to ask, how urgently, what context to include]

Example: "Ask the user directly. Include: what decision is needed, what options
exist, what the consequence of each is. Do not proceed until confirmed."

## On Failure

System default: retry twice, then escalate.

Project refinement: [Fill in — domain-specific retry logic]

Example: "On financial calculation failure: retry once with full data reload.
On second failure: do not estimate — stop and report to user with raw data."

## Destructive Actions

System default: always require confirmation.

Project refinement: [Fill in — what counts as destructive in this project's context]

Example: "Archive moves are auto-confirmed. File deletions always require
explicit user confirmation. Ledger changes are never auto-reversed."

## Domain-Specific Rules

[Fill in: rules specific to this project's domain — compliance, security,
financial thresholds, hours limits, approval chains, etc.]

---

## Agent Behavior in This Project

Agents defined in this project's Agents/ folder operate under:
1. System OPERATING_PROCEDURES.md (floor)
2. This file (project layer)
3. Their own agent file (role-specific behavior)

If an agent file has no special procedure, it inherits from this file.
