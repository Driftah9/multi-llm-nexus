# How Nexus Compares

A cross-reference against existing self-hosted AI platforms, agent frameworks, and LLM interfaces.

> **Note:** This document reflects the state of the ecosystem at the time it was written. The AI tooling landscape moves fast — projects ship major features, change direction, get acquired, or disappear on short timescales. Feature gaps noted here may have been closed. Projects listed may have changed significantly. **Do your own research before making decisions based on this comparison.** Check each project's current documentation, GitHub activity, and release notes. What was accurate when this was written may not be accurate when you are reading it.

---

## The Landscape

There are roughly four categories of tools that overlap with what Nexus does:

| Category | Examples |
|---|---|
| **Self-hosted chat UIs** | Open WebUI, AnythingLLM, Jan.ai |
| **LLM app platforms** | Dify, Flowise, LangFlow |
| **Agent frameworks** | LangChain / LangGraph, CrewAI, AutoGen (Microsoft) |
| **Autonomous agent runners** | AutoGPT, SuperAGI, AgentGPT |

---

## Feature Comparison

| Feature | **Nexus** | Open WebUI | AnythingLLM | Dify | CrewAI / LangGraph |
|---|---|---|---|---|---|
| Self-hosted | ✅ | ✅ | ✅ | ✅ (or cloud) | ✅ (framework) |
| Any LLM provider | ✅ 20 providers | ✅ via Ollama/API | Partial | ✅ | ✅ (code) |
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
