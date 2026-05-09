# Multi-LLM-Nexus

**Your AI platform. You are the Operator. Who you put in the court is up to you.**

Multi-LLM-Nexus is a self-hosted AI agent platform that acts as the hub connecting your infrastructure, your communication platforms, and any LLM you choose. It does not have a personality — you give it one. One user calls theirs a castle and their agent Chamberlin. Another names theirs Terry after Terry Crews. The Nexus doesn't care. It just runs.

---

## What It Is

A persistent, always-on agent platform that:

- **Routes intelligence** across multiple LLM providers — Claude, OpenAI, Ollama (local), Gemini, or any OpenAI-compatible endpoint
- **Connects everywhere** — Mattermost, Discord, Telegram, Slack, Matrix (adapters are pluggable)
- **Acts, it does not just chat** — triage, code, research, support, automation — each task routed to the right model
- **Self-improves** — built-in evolution loop reviews behavior, evaluates new tools, queues changes for your approval
- **Lives on your hardware** — no cloud dependency, no data leaving your network unless you configure it to

---

## The Model

| Role | What It Is |
|---|---|
| **The Nexus** | The platform — infrastructure, routing, memory, self-improvement loop |
| **The Operator** | You — the person who deploys and commands it |
| **Chief of Staff** | The named agent you deploy — your personality layer (call it whatever you want) |
| **Specialists** | Task-specific agents — Triage, Developer, Researcher, Support — powered by whichever LLM fits best |

---

## Architecture

```
src/
  core/          Tick cycle engine, session management, behaviors, triage, commands
  providers/     LLM abstraction layer — swap any model in or out
  tools/         Tool call abstraction — MCP (Claude), function_call (OpenAI), Ollama format
  adapters/      Platform connectors — Mattermost, Discord, Telegram, Slack, Matrix
  setup/         Interactive install wizard
config/
  providers.yaml  Which LLM handles which task type
  adapters.yaml   Platform connection config
```

---

## Provider Support

| Provider | Status | Notes |
|---|---|---|
| Claude Code (CLI) | Reference implementation | Full MCP tool ecosystem |
| Anthropic API | Stable | Direct API, no CLI dependency |
| OpenAI / Compatible | Stable | Works with GPT-4o, Azure, Groq, vLLM, LM Studio |
| Ollama | Stable | Local models, nothing leaves LAN |
| Google Gemini | Planned | |

---

## Platform Adapters

| Platform | Status |
|---|---|
| Mattermost | Reference implementation |
| Discord | Functional |
| Telegram | Functional |
| Slack | Planned |
| Matrix/Element | Planned |

---

## Quick Start

```bash
git clone https://github.com/Driftah9/multi-llm-nexus
cd multi-llm-nexus
./setup.sh
```

The setup wizard will:
1. Ask which LLM provider(s) you want to connect
2. Ask which platform(s) you want to use
3. Offer to deploy self-hosted services (Mattermost, Ollama) via Docker if needed
4. Generate your config files and systemd service
5. Run a live connection test before finishing

---

## Multi-LLM Routing

Configure which model handles which task type in `config/providers.yaml`:

```yaml
providers:
  primary:
    type: claude_code
    model: claude-sonnet-4-6

  triage:
    type: ollama
    model: llama3.2:3b      # fast, local, zero API cost

  code:
    type: openai
    model: gpt-4o

  privacy:
    type: ollama
    model: llama3.1:8b      # nothing leaves the machine

routing:
  default: primary
  triage: triage
  patterns:
    - match: "code|debug|fix|review"
      provider: code
    - match: "private|personal|local"
      provider: privacy
```

---

## Self-Improvement Loop

Nexus includes an optional bi-weekly self-evaluation cycle:

1. **Behavioral review** — reads session signals, updates its own operating profile
2. **Research phase** — evaluates new AI tools, patterns, and techniques
3. **Candidate queue** — findings staged for your approval before any change is made
4. **Evolution log** — every architectural change recorded with measured before/after outcome

No change is made without Operator approval. The loop generates candidates, you decide what ships.

---

## Philosophy

Most AI tools try to be the personality. Nexus hands that back to you.

The platform is neutral infrastructure. The intelligence is interchangeable. The identity is yours.

---

## License

MIT
