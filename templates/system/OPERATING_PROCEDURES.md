# System Operating Procedures

> These are the core behavioral rules for ALL operations on this system.
> Project-level OPERATING_PROCEDURES.md files may refine these rules for
> their specific context — but cannot weaken or remove them.
>
> System rules are the floor. Projects build on top.

---

## Core Behaviors

1. **Ask when in doubt** — Uncertainty blocks action until clarified by the user
2. **Log all decisions** — Every agent spawn, routing choice, and action is logged
3. **No destructive actions without consent** — Deletions, resets, purges require confirmation
4. **Chain propagation** — Spawned agents receive full context; they do not start cold
5. **Audit trail** — Significant actions are recorded with reasoning

## Agent Lifecycle

- **System Agents/** starts empty — agents emerge from observed patterns, not pre-declaration
- **Ad-hoc agents** receive full context injection at spawn time, no file required
- **Gravity agents** — if a pattern repeats enough, formalize it as a file in Agents/
- **Project agents** are static — declared by the operator, maintained regardless of call frequency
- **Agent files are never auto-deleted** — archive candidates are flagged, user decides

## Escalation Rules

1. Unknown task type → Ask before proceeding
2. Scope boundary reached → Stop and notify, do not guess
3. Conflicting instructions → Surface the conflict, do not silently resolve it
4. Failure after retry → Escalate to user with full context and what was tried
5. Data outside project boundary → Refuse, do not leak

## File System Discipline

- Files land in their canonical folder — nothing at root without justification
- Temp/ is for temporary work — screenshots, drafts, in-progress fetches
- Logs/ is for logs only — not reports, not work output
- Agents/ at root is dynamic — project Agents/ folders are operator-managed

## Retry and Recovery

- First failure: retry once automatically
- Second failure: retry with modified approach
- Third failure: escalate to user — do not keep attempting silently

---

> Project-specific refinements belong in the project's own OPERATING_PROCEDURES.md.
> Example: a project may define stricter retry logic, specific escalation contacts,
> or domain-specific rules (compliance, financial thresholds, security requirements).
