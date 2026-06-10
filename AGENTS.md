# Multi-LLM-Nexus — Agent Context

## What This Is

Multi-LLM-Nexus is a self-hosted AI agent platform. LLM-agnostic by design — any provider can be the primary, secondary, or specialist. Adapters connect to communication platforms. The core engine runs the tick cycle, session management, triage, specialist orchestration, and self-improvement loop.

This is the OSS evolution of claude-brain. The key difference: claude-brain is Claude-specific. Nexus abstracts the LLM into a provider layer so any model can run any role.

**Core Principle:** Every refinement is designed for ALL operators, not the deploying user. Operator-specific data (compliance rules, workspace names, channel mappings) lives in config files only. Core code is agnostic.

## Project Path

Runs from the Nexus service account's home directory (e.g. `/home/nexus/multi-llm-nexus/`).

## Source Lineage

Derived from the claude-brain production stack:
- `mattermost-daemon` — production Mattermost adapter + engine
- `claude-daemon` — Telegram/Discord adapter
- `claude-brain-github` — previous OSS snapshot (2026-04-27)

Draw from design patterns. Do not copy VM-specific config, hardcoded IPs, or production credentials.

## Architecture Layers

### providers/ — LLM Abstraction (20+ providers)
Abstract BaseProvider interface. Each provider implements:
- `send(messages, system) -> ProviderResponse`
- `supports_tools() -> bool`
- `format_tool_call(name, args) -> dict`
- `parse_tool_response(response) -> ToolResult`

Implemented: `claude_code.py` (CLI+MCP), `anthropic.py`, `openai.py` (covers Groq/Mistral/DeepSeek/Azure/LM Studio/vLLM via compatible endpoint), `ollama.py` (local), `gemini.py`, `cohere.py`, `bedrock.py`

Registry in `providers/registry.py` — 20+ providers, 100+ models, tier inference (nano/standard/deep).

### core/ — Engine (Provider-Agnostic)
- `engine.py` — tick cycle; routes to orchestrator or standard path per context
- `router.py` — maps task type → provider via providers.yaml
- `session.py` — SessionStore + Session dataclass; provider-agnostic
- `triage.py` — fast classification via configurable triage_provider
- `behaviors.py` — tier/effort/provider commands; cross-platform preferences
- `bridge.py` — unified invoke interface; handles claude_code session IDs + API history; fires `on_provider_change` during failover
- `heartbeat.py` — live status display manager (see Heartbeat System below)
- `orchestrator.py` — workspace-aware specialist dispatcher; real-time agent tracking via heartbeat (see below)
- `specialists.py` — profile loader; parses YAML frontmatter from config/specialists/*.md
- `commands.py` — slash commands
- `watchers.py` — background monitors
- `formatter.py` — output formatting per platform

### adapters/ — Platform Connectors
Each adapter: `connect()`, `listen()`, `send()`, `format_message()`, `disconnect()`
Platform-specific formatting handled in adapter, not core.
Implemented: Mattermost (WebSocket+REST), Discord (REST poll), Telegram.
Planned: Slack, Matrix.

### tools/ — Tool Call Abstraction
MCP is Claude Code specific. Other providers use function_call (OpenAI format) or Ollama tools format.
`base.py` defines ToolCall/ToolResult. Bridges translate to provider-native format.

### config/ — Operator Configuration
- `providers.yaml` — provider definitions and routing rules
- `adapters.yaml` — platform connection settings (bot tokens, channel maps)
- `workspaces.yaml` — workspace categories with specialist routing rules
- `specialists/*.md` — role profiles (YAML frontmatter + markdown system prompt)
- `.env` — secrets (never committed, .env.example provided)

## Specialist Orchestration System

### The Model
- **Workspaces** — operator-defined groupings of adapter contexts (channels, topics, rooms)
- **Specialists** — role-specific agents defined as markdown profiles; any LLM executes them
- **Orchestrator** — engine component; detects workspace context, spawns specialists in parallel, synthesizes output
- **Chief of Staff** — the named agent (bot_name in adapters.yaml); handles synthesis across specialists

### How It Works

```
Message arrives at Engine
    ↓
context in orchestrator-enabled workspace?
    ├─ YES → Orchestrator.dispatch()
    │   ├─ Match keywords → select specialists
    │   ├─ Spawn in parallel (asyncio.gather, ephemeral calls)
    │   ├─ Single specialist → return directly
    │   └─ Multiple → Chief of Staff synthesizes → unified response
    └─ NO → Standard single-session path
```

### Specialist Profile Format

Files in `config/specialists/*.md`. YAML frontmatter + markdown body.

```markdown
---
id: financial
name: Financial Compliance Specialist
tier: standard
tools: [invoice_api, ledger_read]
scope: "Revenue, expenses, tax obligations, compliance thresholds"
---

You are the Financial Compliance Specialist...
[Role definition becomes system_prompt — any LLM can execute it]
```

- `.example` files are ignored by the loader (copy to `.md` to activate)
- Files starting with `_` are ignored (templates/docs)
- `tier` maps to providers.yaml routing (nano/standard/deep)

### Workspace Config Format

```yaml
# config/workspaces.yaml
workspaces:
  business-ops:
    display_name: "Business Operations"
    orchestrator: true
    contexts: [cpa-agent, business-questions, invoice-ninja]
    specialists: [financial, legal, marketing, operations]
    routing_rules:
      - keywords: [tax, nese, expense, revenue]
        specialists: [financial]
      - keywords: [contract, compliance, filing]
        specialists: [legal, financial]
    default_specialists: [financial]
    specialist_tier: standard
    synthesis_tier: standard
```

### Provided Example Profiles
- `financial.md.example` — Financial Compliance Specialist
- `legal.md.example` — Legal & Compliance Specialist
- `marketing.md.example` — Marketing Strategy Specialist
- `operations.md.example` — Operations Manager Specialist

## Heartbeat System (Live Status Display)

### What It Is
A live-updating status display showing what the Nexus is doing at any moment. No other AI platform shows this — most show a typing dot.

### Display Format
```
_{prefix} · {model} [· {effort}] [— {agents}] — {phase} {elapsed}_
```

- `prefix` — Provider category: "Claude", "Gemini", "OpenAI", "Groq", "Local" (from `display_prefix` in config)
- `model` — Human-friendly model name: "Opus", "1.5-pro", "tinyllama" (from `model_display` in config)
- `effort` — Optional, only shown when provider supports effort levels (Claude, OpenAI o3)
- `agents` — Active specialist names (≤3: listed, >3: count). Decrements in real-time.
- `phase` — "triaging", "thinking", "working", "synthesizing"
- `elapsed` — Time since request started: "30s", "1m12s"

### Architecture
- `HeartbeatManager` (src/core/heartbeat.py) — state holder + background 30s tick loop
- **Adapter-agnostic**: adapter provides `push_fn(post_id, text)` callback (Mattermost PATCH, Discord edit, etc.)
- **Zero LLM tokens**: all bookkeeping is mechanical (asyncio tasks, not reasoning)
- `ProviderChainEntry` carries `display_prefix`, `model_display`, `effort_levels` (from providers.yaml)
- `on_provider_change` callback fires during failover — heartbeat updates instantly
- Orchestrator calls `heartbeat.set_agents()` at dispatch, decrements as specialists complete

### Visual Examples
```
Claude · Opus · high — thinking 30s
Claude · Sonnet · standard — financial, security — working 1m12s
Claude · Orchestrator — 4 agents active — working 2m30s
Gemini · 2.0-flash · thinking — working 55s
OpenAI · o3 · reasoning — working 2m05s
Local · tinyllama — thinking 15s
Local · llama3:70b — working 1m45s
```

### Provider Display Taxonomy
| Connection type | Display prefix | Shown as |
|---|---|---|
| claude_code (CLI) | Claude | Claude · Opus · high |
| anthropic (API) | Claude | Claude · Sonnet · standard |
| gemini | Gemini | Gemini · 2.0-flash · thinking |
| openai | OpenAI | OpenAI · gpt-4o |
| openai (o3) | OpenAI | OpenAI · o3 · reasoning |
| groq | Groq | Groq · llama3-70b |
| ollama / local | Local | Local · tinyllama |

The display shows WHAT is active, not HOW it's connected. Connection details are config — the heartbeat is operational status.

## Key Design Rules

1. **No hardcoded LLM** — everything goes through the router
2. **No hardcoded platform** — everything goes through adapters
3. **No operator-specific data in core** — SSDI rules, company names, channel lists belong in operator config
4. **Public-safe** — no real credentials, IPs, or VM specifics in any file
5. **No hardcoded paths or domain language** — use `Path.home()` or config values; never assume a username, home directory, or what the operator calls their work folders
6. **Ollama = zero-cost local tier** — anyone can run without API keys
7. **OpenAI-compatible endpoint** — one provider covers OpenAI, Azure, Groq, LM Studio, vLLM
8. **Specialists are stateless** — ephemeral one-shot queries, no history accumulation
9. **Bypass gracefully** — if no workspace match or all specialists fail, fall through to standard path
10. **Heartbeat is mechanical** — status display uses zero LLM tokens; all state tracking is asyncio bookkeeping

## Current Status (v0.6.3 — 2026-06-05)

### Changes since v0.6.0
- **v0.6.1** — full documentation audit; claude-brain feature port marked complete.
- **v0.6.2** — OpenAI-compatible API adapter added; vLLM provider + model lifecycle manager for hardware-agnostic local inference.
- **v0.6.3** — `PoolRouter` (cost-class-aware provider selection from tier pools); `UserGate` (two-stage pre-triage authorization: ACL + local-LLM intent check); Triage extended to 5 dimensions (urgency, task_value, capability, estimated_complexity, task_type); `AdapterBase` + `APISenderBase` extracted from the MM/Discord/Telegram adapters; `cost_class` added to `ProviderRegistry`; mesh docs restructured to four modes (0/A/B/R, ONS removed in favor of IPFS/libp2p transport).

### Baseline (carried forward from v0.6.0)
Providers layer complete (8 provider-type adapters fronting 22 selectable providers — the `openai` type reaches Groq/Mistral/DeepSeek/Cerebras/etc. via `base_url`). Core engine + router + triage + behaviors + bridge all complete.
Orchestrator + specialist loader built with real-time heartbeat integration. Session class fixed.
Mattermost adapter fully implemented. Discord + Telegram adapters implemented.
Config examples for providers, adapters, workspaces, and 4 specialist profiles provided.

**Heartbeat system** implemented: `HeartbeatManager` + `HeartbeatState` in `src/core/heartbeat.py`.
Provider chain entries now carry `display_prefix`, `model_display`, `effort_levels` for heartbeat display.
Bridge fires `on_provider_change` callback during failover. Orchestrator tracks agent list in real-time
with decrement as specialists complete.

**Self-eval layer** added: `skill_metrics.py`, `session_state.py`, `triage_validator.py`, `session_summary.py`.
`!specialists` and `!spaces` commands implemented. RAG store + memory loader + session store ported.
ProviderChain `try_with_fallback()` returns fallback_occurred flag for response tagging.

GitHub: git@github.com:Driftah9/multi-llm-nexus.git

## What's Next

- Slack + Matrix adapters
- Triage accuracy report script — reads validator DB, surfaces misclassification patterns
- Self-improvement loop (eval → candidate queue → operator approval)
- LLM Watcher integration — standalone health monitoring service with state announcements
- Live thread context fetching in MM adapter (last 20 posts → orchestrator dispatch)
