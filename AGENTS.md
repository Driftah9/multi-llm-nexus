# Multi-LLM-Nexus — Agent Context

## What This Is

Multi-LLM-Nexus is a self-hosted AI agent platform. LLM-agnostic by design — any provider can be the primary, secondary, or specialist. Adapters connect to communication platforms. The core engine runs the tick cycle, session management, triage, and self-improvement loop.

This is the OSS evolution of claude-brain (production instance at 10.0.0.7). The key difference: claude-brain is Claude-specific. Nexus abstracts the LLM into a provider layer so any model can run any role.

## Project Path

`/home/claude/projects/multi-llm-nexus/`

## Source Reference

The production claude-brain implementation lives at:
- `/home/claude/projects/mattermost-daemon/` — production Mattermost adapter + engine
- `/home/claude/projects/claude-daemon/` — Telegram/Discord adapter (if exists)
- `/home/claude/projects/claude-brain-github/` — previous OSS snapshot (2026-04-27)

Draw from these when implementing. Do not copy VM-specific config, hardcoded IPs, or production credentials.

## Architecture Layers

### providers/ — The Core Innovation
Abstract BaseProvider interface. Each provider implements:
- `send_message(session, prompt) -> str`
- `supports_tools() -> bool`
- `format_tool_call(tool_name, args) -> dict`
- `parse_tool_response(response) -> ToolResult`

Providers: `claude_code.py` (subprocess), `anthropic.py` (API), `openai.py` (API + compatible), `ollama.py` (local), `gemini.py` (planned)

### core/ — Engine (Provider-Agnostic)
- `engine.py` — tick cycle, main loop
- `router.py` — maps task type to provider based on providers.yaml
- `session.py` — conversation state, provider-agnostic
- `triage.py` — fast classification using configurable triage_provider
- `behaviors.py` — rule engine (unchanged from claude-brain)
- `commands.py` — slash commands
- `watchers.py` — background monitors
- `formatter.py` — output formatting per platform

### adapters/ — Platform Connectors
Each adapter: `connect()`, `listen()`, `send()`, `format_message()`, `disconnect()`
Platform-specific formatting handled in adapter, not core.

### tools/ — Tool Call Abstraction
MCP is Claude Code specific. Other providers use function_call (OpenAI format) or Ollama tools format.
`base.py` defines ToolCall/ToolResult. Bridges translate to provider-native format.

## Key Design Rules

1. **No hardcoded LLM** — everything goes through the router
2. **No hardcoded platform** — everything goes through adapters
3. **Public-safe** — no real credentials, IPs, or VM specifics in any file
4. **Ollama = zero-cost local tier** — anyone can run it without API keys
5. **OpenAI-compatible endpoint** — one provider covers OpenAI, Azure, Groq, LM Studio, vLLM

## Config Files

`config/providers.yaml` — provider definitions and routing rules
`config/adapters.yaml` — platform connection settings
`.env` — secrets (never committed, .env.example provided)

## Current Status

Project initialized 2026-05-09. Structure scaffolded. Implementation in progress.
Priority order: providers/base.py → providers/claude_code.py → core/router.py → everything else
