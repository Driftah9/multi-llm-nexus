# Lineage

## Where Nexus Came From

Nexus was not built from scratch. It was not coded by a team of AI researchers. It evolved from a single operator's attempt to build something that worked the way they needed it to work.

This document is an honest account of what inspired it, what was borrowed, what was adapted, and what was built fresh. Transparency about lineage is not a weakness — it is how open source works. Nexus inherits from a lineage of projects that each solved one piece of a larger problem.

---

## The Starting Point

The foundation was **Anthropic's Claude CLI** (Claude Code) — a command-line tool that lets you run Claude in a terminal session. That was the seed. The question was: what happens if you give it a server to live on, persistent infrastructure, and a way to receive messages from communication platforms?

That question became **claude-brain**: a single-VM, always-on agent running on Ubuntu 24.04 that routes messages from Mattermost to Claude and back. Not a framework, not a product — a working deployment with one user.

**Multi-LLM-Nexus is the generalization of claude-brain** — the same architecture extracted from operator-specific configuration and made installable by anyone.

---

## What Was Borrowed and From Where

### Memory Architecture ← mempalace, obsidian-mind, obsidian-wiki

Three open-source projects contributed patterns to how Nexus manages persistent knowledge:

**[mempalace](https://github.com/mempalace/mempalace)** — introduced the idea of categorizing memory by type and retrieval purpose, not storing everything in a flat file. Nexus's typed memory system (user, feedback, project, reference) reflects this philosophy: different kinds of memory serve different purposes and should be stored and retrieved differently.

**[obsidian-mind](https://github.com/breferrari/obsidian-mind)** — demonstrated markdown-based knowledge that is human-readable, version-controllable, and navigable through links. Nexus's memory files are plain markdown with frontmatter. A human can open them, read them, and understand them without tooling.

**[obsidian-wiki](https://github.com/Ar9av/obsidian-wiki)** — contributed the pattern of a lightweight index on top of detailed knowledge files. Nexus's MEMORY.md is an index, not the content. Detailed files are read on demand. The distinction matters for context window efficiency: load the index always, load the detail when it's relevant.

What was taken: the **categorization principle**, the **markdown-as-storage approach**, and the **two-tier index/detail loading pattern**.

What was not taken: the actual code, the vault architecture, or the bidirectional link system. Nexus does not use Obsidian. It uses plain files.

### Adapter Architecture ← OpenClaw

**[OpenClaw](https://github.com/openclaw/openclaw)** — an agent harness built by a community of developers focused on multi-platform communication (Telegram, Discord, Slack, WhatsApp, iMessage, and more). OpenClaw's architectural contribution to Nexus was the **adapter pattern**: communication platforms should be pluggable, not hardcoded. The agent should not know or care whether it's talking to Telegram or Mattermost — the adapter abstracts that away.

This became the foundation of Nexus's `src/adapters/` layer.

OpenClaw also contributed four specific operational patterns that were ported and deployed on the claude-brain production system first, then formalized:

| Pattern | What It Does |
|---|---|
| **Inbound debounce** | Prevents rapid duplicate messages from processing twice |
| **Cache key determinism** | Normalizes system prompts before API calls to improve cache hit rates |
| **Thread binding policy** | Per-channel control over whether threads share conversation context |
| **Central security layer** | Scope-based authorization before any action reaches the provider |

These are implemented in `src/core/` and are the first four entries in the Nexus skill registry.

What was taken: the **adapter abstraction principle** and four **operational patterns**.

What was not taken: OpenClaw's Node.js runtime, its plugin SDK, its marketplace, or its specific platform implementations.

### Skill Format ← Hermes and OpenClaw (convergent)

**[Hermes](https://github.com/nousresearch/hermes-agent)** (Nous Research) and **OpenClaw** independently arrived at the same skill format: a `SKILL.md` file with YAML frontmatter defining metadata, combined with a directory structure for supporting assets. Both projects treat skills as declarative markdown, not programmatic plugins.

Nexus adopted this format for the `skills/` registry. The format is compatible with both projects — a skill file that works in Nexus should be readable by Hermes and vice versa.

What was taken: the **SKILL.md format and directory convention**.

What was not taken: Hermes's skill marketplace (Skills Hub), its Curator background agent, or its autonomous skill creation loop.

---

## What Was Built Fresh

The following components do not derive from any external project. They were designed to solve specific problems that existing tools did not address:

**Operator/core separation** — The explicit distinction between what belongs to the platform (routing, session management, provider abstraction) and what belongs to the operator (workspace config, specialist profiles, behavioral rules, memory content). Nexus enforces this at the architecture level. Other tools in this space mix the two.

**Metrics-first skill evolution** — The hypothesis that skills should be measured before they are formalized, and that deployment data should inform refinement candidates before they reach the operator. Hermes generates skills autonomously. Nexus collects runtime metrics (SQLite via `skill_metrics.py`), stages candidates for operator approval, and will surface findings via a report generator (planned — not yet implemented). This is the inverse of the Hermes approach — measure first, formalize second.

**Cognitive profile** — An explicit, auditable, human-readable description of how the operator thinks, signals intent, and makes decisions. This is not a machine learning model. It is a markdown file that gets updated when something is learned about the operator. It can be read, corrected, and disagreed with. No black box.

**Approval gate** — All improvements — memory updates, skill candidates, behavioral rule changes — require operator review before activation. This is a deliberate choice against autonomous self-modification. For a system running production infrastructure, compliance-sensitive workflows, or personal financial data, the operator is the final decision-maker.

**ACTIVE/STANDBY hybrid engine** — The core engine model where one session is active and others are on standby, with idle-triggered distillation and cross-session context injection. This was engineered from observed usage patterns on the claude-brain production system.

**Tier routing** — `nano` / `standard` / `deep` as provider-agnostic tier names that route to whatever model the operator configured. No model names in application logic. When you upgrade from llama3.1:8b to llama3.1:70b, one line changes in `providers.yaml`. The rest of the system is unchanged.

---

## The Production Test Environment

Everything above was validated on a real, production deployment before being included in Nexus. The claude-brain system — a Ubuntu 24.04 VM running on a home lab — has been the proving ground. Patterns that worked there were extracted and generalized. Patterns that seemed good in theory but broke in production were dropped or redesigned.

In June 2026 this relationship was made explicit rather than incidental. The live system had drifted toward provider-specific behavior over time — it was, after all, one operator with one model wired in. A deliberate convergence pass rewrote how it reads system memory, fails over between providers, classifies work, and runs local tools so that none of it assumes a particular model, then ported the provider-neutral results down. From that pass Nexus gained: a single memory contract so any model reads system memory the same way; failure classification that drives retry-versus-advance-versus-stop in the failover chain; a capability gate that keeps a feature dark until the hardware or the providers can actually support it, and lights it up automatically when they can; multi-orchestrator failover that is itself dark on a single-provider floor; cross-adapter identity resolution; and on-box web extraction in place of a remote reader. The discipline held throughout: only mechanism crossed the line — no provider keys, no roster, no operator data. The full account is in `CHANGELOG.md` (0.8.0) and `docs/convergence-2026-06.md`.

This is an advantage and a limitation. The advantage: Nexus reflects operational reality, not theoretical design. The limitation: it was tested by one operator, with one set of use cases, on one hardware configuration. Other operators will find edges that were never encountered.

The roadmap, the production readiness checklist, and the known gap list in `docs/comparison.md` reflect what is known to be missing.

---

## Acknowledgments

- **Anthropic / Claude Code** — the foundation
- **mempalace** — typed memory categorization
- **obsidian-mind** and **obsidian-wiki** — markdown knowledge graph pattern
- **OpenClaw** — adapter architecture and four operational patterns
- **Hermes (Nous Research)** — SKILL.md format validation and skill lifecycle thinking

None of these projects endorsed Nexus. None were forked. The relationships are intellectual — concepts studied, patterns understood, implementations written independently.

---

## On Being One Person

Nexus was designed by one person who is not primarily a software developer. The architectural decisions — what to separate, what to measure, when to require approval — came from thinking about how systems should fit together, not from writing the code that implements them.

The code that exists is functional. It has rough edges. It will have bugs other operators find that the original operator never hit. The production readiness checklist in `docs/comparison.md` is honest about what is missing.

What the project has that most one-person projects lack: a deployment that has been running continuously, handling real traffic, with a real feedback loop that has been improving it for months. The ideas were tested. The patterns held. The gaps are known.

That is a reasonable foundation for a public release.
