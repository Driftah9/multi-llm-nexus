# How Nexus Compares

A cross-reference against existing self-hosted AI platforms, agent frameworks, and LLM interfaces.

> **Note:** This document reflects the state of the ecosystem at the time it was written. The AI tooling landscape moves fast — projects ship major features, change direction, get acquired, or disappear on short timescales. Feature gaps noted here may have been closed. Projects listed may have changed significantly. **Do your own research before making decisions based on this comparison.** Check each project's current documentation, GitHub activity, and release notes. What was accurate when this was written may not be accurate when you are reading it.

---

## The Landscape

There are roughly five categories of tools that overlap with what Nexus does:

| Category | Examples |
|---|---|
| **Agent harnesses** | Hermes (Nous Research), OpenClaw |
| **Self-hosted chat UIs** | Open WebUI, AnythingLLM, Jan.ai |
| **LLM app platforms** | Dify, Flowise, LangFlow |
| **Agent frameworks** | LangChain / LangGraph, CrewAI, AutoGen (Microsoft) |
| **Autonomous agent runners** | AutoGPT, SuperAGI, AgentGPT |

The agent harness category — Hermes and OpenClaw — is where most people comparing Nexus will land. That comparison gets its own section first.

---

---

## Hermes and OpenClaw: The Closest Comparisons

Most people evaluating Nexus have already looked at Hermes (Nous Research) or OpenClaw. These are the right tools to compare against — all three are agent harnesses, not chat UIs or app builders.

| Feature | **Nexus** | **Hermes** | **OpenClaw** |
|---|---|---|---|
| **Primary runtime** | Python daemon on your hardware | Python daemon, local or VPS | Node.js daemon, local or VPS |
| **Installation** | `git clone` + `./setup.sh` wizard | One-command curl installer | One-command curl or npm |
| **Platform adapters** | Mattermost, Discord, Telegram | 21 platforms (Telegram, Discord, Slack, WhatsApp, Signal, Mattermost, Matrix, more) | 20+ platforms including iMessage, Line, Zalo |
| **LLM providers** | 22 direct providers + any Ollama/OpenAI-compat endpoint | 30 providers, OpenRouter as primary aggregator | Provider-agnostic via config |
| **Memory model** | Typed files (user/feedback/project/reference), structured index, operator-maintained | Two flat files (USER.md 1,375 chars, MEMORY.md 2,200 chars), hard char limits, agent-curated | Two flat files, similar to Hermes |
| **Memory plugins** | No plugin system (by design) | 7 plugins: Honcho, Holographic, mem0, hindsight, and others | Honcho-compatible (less integrated) |
| **Cross-session recall** | Planned (ChromaDB RAG, not deployed) | FTS5 keyword search on session history | Session search via flat file |
| **Skill format** | SKILL.md with YAML frontmatter (v0.4) | SKILL.md with YAML frontmatter | SKILL.md with YAML frontmatter |
| **Skill creation** | Measurement → metrics → candidate → operator approval *(measurement + gating built; report generator planned)* | LLM-prompted: agent creates skills autonomously during sessions | Marketplace (ClawHub) + manual |
| **Skill metrics** | Runtime SQLite collection → self-eval report *(SQLite collector built; report generator not yet implemented)* | Usage tracking (skill_usage.py), not fed back to refinement | No runtime metrics |
| **Self-improvement loop** | Bi-weekly: behavioral review → research → candidate queue → approval | Curator (background, 7-day cycle): lifecycle management, not content refinement | None |
| **Operator/core separation** | Explicit: workspace config vs. core logic | None: single config per deployment | Partial: owner plugin vs. core SDK |
| **Cron/scheduling** | External scripts → MM notification | Built-in, per-job profiles, platform delivery | Built-in |
| **Subagents** | Specialist dispatch (parallel) | Full AIAgent copies (depth 1 default, cap 3) | ACP spawn system |
| **Approval gate** | All candidates require operator approval before deployment | Autonomous: agent creates/edits skills without approval | Not applicable |
| **Config model** | YAML — providers.yaml, adapters.yaml, workspaces.yaml | Single config.yaml + .env per profile | YAML + .env |
| **Codebase scale** | Moderate — tighter files | Large (cli.py 660KB, mid-refactor) | Moderate — strict SDK boundaries |
| **Tests** | Not measured | ~17,000 tests across ~900 files | Not measured |
| **License** | MIT | MIT | MIT |

### Where Hermes Wins

- **Install experience** — one command, guided onboarding, works on Linux/Mac/VPS immediately
- **Platform breadth** — 21 adapters including iMessage, Line, WeChat, email, SMS
- **Memory plugins** — Honcho for behavioral profiling is a genuine differentiator; 7 memory backends available
- **Session search** — FTS5 across conversation history is live; Nexus's RAG is planned but not deployed
- **Test coverage** — 17k tests is a serious engineering investment
- **Autonomous skill building** — agent creates skills from experience without operator input (if you trust it to)
- **Community** — larger adoption, more documentation, more third-party content

### Where OpenClaw Wins

- **Install experience** — single npm command, guided wizard, native on all desktop platforms
- **Platform breadth** — 20+ adapters, widest platform support in the category
- **SDK boundaries** — strict plugin/core separation, contracts well-defined
- **Skill marketplace** — ClawHub gives access to community-built skills (with security caveats)
- **Community** — large early adopter base, extensive documentation

### Where Nexus Is Different

**Operator control is not optional.** In Hermes, the agent creates skills autonomously. In Nexus, all changes surface as candidates waiting for your review. For operators running production infrastructure, compliance-aware systems, or environments where an autonomous skill creation could cause problems, this matters.

**Memory is structured, not size-limited.** Hermes enforces 3,575 total characters across memory files, forcing the agent to decide what to keep. Nexus uses typed files (user, feedback, project, reference) with explicit purpose. Neither approach is wrong — they reflect different philosophies: Hermes trusts the agent to curate; Nexus trusts the operator.

**Metrics pipeline before skill creation.** Nexus measures deployed modules before formalizing them as skills. Hermes ships skills without runtime feedback. The hypothesis: a skill that's measured and refined is worth more than a skill that was simply generated.

**Operator/core separation is explicit.** Workspace config, specialist profiles, and behavioral rules are operator data. The routing engine, session management, and provider abstraction are core. These two layers are not mixed. Nexus targets operators who want to understand exactly what is theirs and what is the platform.

**It is not trying to be Hermes.** Hermes has a mission, a following, and a growth trajectory. Nexus was built by one operator to solve a specific problem — a persistent, always-on AI layer that lives on your infrastructure and works through the channels you already use. That scope is intentional.

### Honest Assessment

If you want the fastest path to a working agent, well-documented onboarding, and the largest community: **use Hermes**.

If you want a platform where every architectural decision is auditable, operator approval is required before anything changes, and the separation between "what the platform does" and "what the operator configured" is explicit: **Nexus is worth the additional setup**.

---

## Feature Comparison (Against UI Tools and Frameworks)

| Feature | **Nexus** | Open WebUI | AnythingLLM | Dify | CrewAI / LangGraph |
|---|---|---|---|---|---|
| Self-hosted | ✅ | ✅ | ✅ | ✅ (or cloud) | ✅ (framework) |
| Any LLM provider | ✅ 22 providers | ✅ via Ollama/API | Partial | ✅ | ✅ (code) |
| Persistent daemon | ✅ Always-on | ❌ Web app | ❌ Web app | ❌ Web app | ❌ Library |
| Mattermost adapter | ✅ | ❌ | ❌ | ❌ | ❌ |
| Discord adapter | ✅ | ❌ | ❌ | ❌ | ❌ |
| Telegram adapter | ✅ | ❌ | ❌ | Webhook only | ❌ |
| Auto tier routing | ✅ nano/standard/deep | ❌ | ❌ | Manual flows | Code only |
| No browser required | ✅ | ❌ | ❌ | ❌ | ✅ (code) |
| Local hardware flexibility | ✅ GPU, phones, any endpoint | Limited | Limited | ❌ | ❌ |
| Identity belongs to operator | ✅ | ❌ | ❌ | ❌ | N/A |
| Config-based (no visual builder) | ✅ YAML | Partial | Partial | ❌ GUI-first | ✅ (code) |
| Self-improvement loop | ✅ Built in | ❌ | ❌ | ❌ | ❌ |

---

## Where the Others Win

Being direct about it:

**Open WebUI**
Better browser-based chat experience. Strong Ollama model management, user accounts, image generation, document upload, and a polished UI. If you want to point non-technical users at a web interface, Open WebUI is the better tool. Nexus has no web UI — by design.

**Dify**
More capable for building end-user-facing LLM applications. Visual workflow builder, RAG pipelines, API publishing, team workspaces. If you are building a product for other people to use, Dify is more suited to that. Nexus is built for the operator using it themselves.

**AnythingLLM**
Lower barrier to entry for non-technical users. Workspace concept is intuitive, document Q&A is built in. Good for a team that wants local LLM access without editing YAML files.

**CrewAI / LangGraph**
More expressive for developers who want to code complex multi-agent task graphs in Python. More flexible, more brittle, requires more ongoing maintenance. Nexus trades some expressiveness for operational reliability.

**AutoGen (Microsoft)**
Strong for research and multi-agent conversation experiments. Active research backing, large community. More experimental by design — not an always-on production daemon.

---

## Where Nexus Is Different

**It lives on your communication platforms — not in a browser tab.**
Every other tool in this space is a web application you open when you want to use it. Nexus runs as a daemon and responds in the channels where work already happens — Mattermost, Discord, Telegram. No context switch. No tab to keep open.

**Tier routing is automatic.**
No other self-hosted platform automatically routes a message to a fast cheap model for simple questions and a capable model for complex ones, per message, without you touching a thing. You configure the tiers once; Nexus classifies and routes from that point forward.

**The provider abstraction is genuine.**
Most platforms have a primary model and optionally some alternatives. In Nexus, triage, standard, deep, and specialist can each be a completely different provider from a completely different company running on completely different hardware — including a shelf of phones running LineageOS in the same room.

**The identity belongs to the operator.**
Open WebUI is Open WebUI. Dify is Dify. When you deploy Nexus, it runs under whatever name you give it. The platform has no brand presence in your environment.

**It is a daemon, not an application.**
It starts, it runs, it handles messages. No logged-in browser session required. No timeout. No "your session has expired." It is infrastructure, not an app.

---

## Honest Gaps

| Gap | Notes |
|---|---|
| No web UI | Intentional — but non-technical users have nothing to point a browser at |
| No built-in RAG pipeline | Embeddings provider support exists; document ingestion pipeline not yet built |
| No user accounts or multi-tenant | Single-operator focused; no per-user permissions or isolated workspaces |
| Smaller community | New project vs. established ecosystems with thousands of users |
| No plugin marketplace | Extending behavior requires editing code or config |

---

## The One-Line Version

Everything else is either a chat interface for interacting with models through a browser, or a developer framework for building LLM apps for other people to use. Nexus is a persistent infrastructure layer that connects your AI capability to the platforms where you already work — and it does not care which LLM is doing the work.

---

## A Final Word

This comparison was written at a specific point in time by the people building Nexus. It is not a neutral third-party review. The intent is to give an honest picture — including where other tools are the better choice — but you should verify the claims here against current sources before drawing conclusions.

The AI tooling space changes faster than most documentation can keep up with. If you are evaluating Nexus against an alternative, spin both up, run your actual workload, and let the result speak for itself. No comparison document replaces that.
