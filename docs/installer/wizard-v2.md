# Nexus Setup Wizard — v2 Design

**Status:** Design / Pre-build  
**Date:** 2026-06-16  
**Context:** Based on live install testing on a clean Ubuntu VM (nexus-test-production, 24-core CPU, 19.5 GB RAM, no GPU). This doc captures the full redesign before implementation.

---

## Problems with Wizard v1

| Problem | Impact |
|---|---|
| Cloud infrastructure as a separate pass | Confused users — Bedrock/Azure aren't "infrastructure", they're providers |
| Platform adapters asked before any LLM is live | Mattermost setup requires a working AI; token collection at this stage is premature |
| Role assignment is fully interactive (5 prompts) | Users don't know which LLM should handle triage; system should reason this itself |
| Ollama endpoint defaults to `localhost` | Breaks any user on a different machine on LAN |
| Ollama model not pulled automatically | Leaves user with a configured-but-empty provider |
| Claude CLI: print URL and skip | Should install the CLI and guide auth in the same flow |
| Use case never selected | Workspace has no identity or structure |
| System name defaults to orchestrator name | System name should be the machine hostname |
| No pause after password display | Password scrolled off screen before user could read it |
| `bash: no job control` on shell handoff | TTY not fully transferred through `su -c "...;exec bash -li"` |
| Whiptail not ensured | Multi-select UI depends on it but it's not checked/installed |

---

## Wizard v2 — Revised Flow

### Phase 0: Root handoff (install.sh)

Root installer is kept minimal. Its only job is system prep and user creation:

1. System check — Python 3.11+, git, curl, whiptail (install any missing)
2. Create bot user + generate password
3. **Pause after password display** — `Press Enter to continue` before handoff
4. Write narrow sudoers rule
5. Write `.nexus-install-config` and `.nexus-bootstrap.sh`
6. `exec su - $USERNAME -c "bash ~/.nexus-bootstrap.sh; exec bash -li"`

**TTY fix:** Use `script -q -c "bash ~/.nexus-bootstrap.sh" /dev/null` instead of direct `su -c` to properly allocate a PTY for the bootstrap process, enabling job control and whiptail's full-screen menus.

---

### Phase 1: Bootstrap (bot user)

Runs as the bot user. Handles everything the root phase handed off:

1. `mkdir -p ~/Logs` → log to `~/Logs/install.log`
2. Clone repo
3. Scaffold 16 system root folders
4. Install identity templates
5. Create venv + install base pip deps
6. **Launch wizard**

---

### Wizard Step Order (v2)

```
[A] System Scan
[B] System Identity
[C] Hardware Detection & Local LLM Recommendation
[D] Provider Selection  ← merged flat list; whiptail checklist
[E] Adapter Selection   ← whiptail checklist; follow-up per adapter
[F] Provider Configuration  ← install CLIs, enter API keys, test connections
[G] Role Assignment     ← auto-derived; only asks "who is orchestrator" if >1 provider
[H] Platform Setup      ← docker-compose generation; token collection deferred
[I] Service install
[J] Done
    ↓ (future Phase 2 — after first LLM is live)
[K] Use case selection  ← LLM-assisted; workspace folder named from result
[L] Triage/worker auto-assignment ← LLM self-reasons from available providers
```

---

### [A] System Scan

Same as v1 — good output. Checks CLI tools, Python packages, local services, API keys.

One addition: show **system IP** prominently — used as default for all local service URLs.

```
System IP: 10.0.0.121  (used as default for all local service endpoints)
```

---

### [B] System Identity

- **Orchestrator name** — what users call the AI. Default: none (must choose). Example: `chamberlain`
- **System name** — the machine's identity. Default: `$(hostname)`. Example: `nexus-test-production`

The system name is NOT the bot's name. It identifies the hardware/VM this is running on.

---

### [C] Hardware Detection & Local LLM

Same hardware scan (CPU cores, RAM, GPU). Then:

- Show what the hardware can support (RAM thresholds):
  - < 8 GB: local LLM not recommended
  - 8–16 GB: `phi4-mini`, `qwen2.5:3b`, `llama3.2:3b`
  - 16–32 GB: `llama3.1:8b`, `mistral:7b`
  - 32 GB + GPU: `llama3.1:70b`, `qwen2.5:32b`

- Show ONLY models that fit the hardware. No showing 70B to a CPU-only 19.5 GB system.
- If local LLM is viable: "Include a local LLM? (Y/n)" → adds `ollama` to the preselected provider list
- If not viable: skip the local LLM section entirely

---

### [D] Provider Selection — Flat Merged List

**UI:** `whiptail --checklist` (arrow keys + spacebar + Done). Fallback: comma-separated numbers.

One flat list — cloud providers AND cloud infrastructure in a single menu. Bedrock/Azure/Vertex are tagged `[Enterprise]` to distinguish them visually but they're not a separate pass.

```
 Select providers (SPACE to select, ENTER when done):
 [ ] Anthropic / Claude — CLI subscription
 [ ] Anthropic / Claude — API key
 [ ] OpenAI (GPT-4o, o3)
 [ ] GitHub Models (free tier)
 [ ] OpenRouter (100+ models)
 [ ] Google Gemini (Flash, Pro)
 [ ] Groq (fast inference, free tier)
 [ ] Mistral AI
 [ ] DeepSeek (V3 + R1)
 [ ] xAI / Grok
 [ ] Cohere (RAG-optimized)
 [ ] Together.ai
 [ ] Fireworks.ai
 [ ] Perplexity (web search)
 [ ] Hugging Face
 [ ] Cerebras
 [*] Ollama (local — pre-selected from hardware scan)
 [ ] LM Studio
 [ ] vLLM
 [ ] Amazon Bedrock  [Enterprise]
 [ ] Azure OpenAI    [Enterprise]
 [ ] Google Vertex   [Enterprise]
```

---

### [E] Adapter Selection

**UI:** `whiptail --checklist` — same pattern.

```
 Select platforms (SPACE to select, ENTER when done):
 [ ] Mattermost  (self-hosted team chat)
 [ ] Discord
 [ ] Telegram
```

**Follow-up per selected adapter:**

For adapters that have a local hosting option (Mattermost, Discord bot):

```
Mattermost: do you want to run a local instance or connect to an existing server?
  (1) Set up local instance (Docker)
  (2) Connect to existing server

Discord: do you want to run the bot locally or connect an existing bot?
  (1) Set up local Docker bot
  (2) Connect existing bot token
```

**Telegram:** No local instance. Just note: "You'll need to create a bot via @BotFather after install."

**Token collection:** DEFERRED for all adapters. The wizard notes what's needed but does not ask for tokens yet. Tokens are configured after the system is live.

---

### [F] Provider Configuration

For each selected provider, in order:

**Anthropic CLI (subscription):**
1. Check if `claude` CLI is on PATH
2. If not found: `curl -fsSL https://claude.ai/install.sh | sh`
3. After install: prompt `claude auth login` and wait for confirmation
4. Test: `claude -p "ping" --output-format text` → confirm response
5. If test passes: mark configured. If fails: offer to skip and continue.

**API key providers (OpenAI, Gemini, Groq, etc.):**
1. Prompt for API key (masked input)
2. Test connection immediately
3. Must pass to proceed (or explicitly skip)

**Ollama (local):**
1. Check if Ollama is running at `http://[SYSTEM_IP]:11434`
2. If not found: offer to install (`curl -fsSL https://ollama.com/install.sh | sh`)
3. After install/confirm running: pull the recommended model **in background** with progress indicator
4. Endpoint stored as `http://[SYSTEM_IP]:11434` — never `localhost`

---

### [G] Role Assignment (Auto-Derived)

**If exactly 1 provider configured:** That provider is the orchestrator. No question asked.

**If multiple providers:** Single question: `Who is the orchestrator? (handles primary conversations)`  
→ whiptail radiolist with configured providers.

**Triage, workers, failover:** NOT configured in the wizard. Derived at runtime:
- Triage: fastest available provider (Groq > Cerebras > local small model > orchestrator)
- Failover: local LLM if available; otherwise smallest cloud provider
- Workers: the orchestrator assigns work to the pool at runtime

This is where Phase 2 (LLM-assisted reasoning) takes over. The runtime engine self-assigns roles on first startup based on latency benchmarks and capability probes.

---

### [H] Platform Setup (Docker compose generation)

For each adapter selected as "local instance":

**Mattermost local:**
```
~/dockers/mattermost/
├── docker-compose.yml   ← generated
└── README.txt           ← instructions: start, create bot user, get token
```

docker-compose.yml:
```yaml
version: '3.8'
services:
  mattermost:
    image: mattermost/mattermost-team-edition:latest
    ports:
      - "8065:8080"
    environment:
      MM_SERVICESETTINGS_SITEURL: "http://[SYSTEM_IP]:8065"
    volumes:
      - ./data:/mattermost/data
      - ./logs:/mattermost/logs
      - ./config:/mattermost/config
    restart: unless-stopped
```

Wizard shows:
```
  Mattermost docker-compose written to ~/dockers/mattermost/
  Start it:  docker compose -f ~/dockers/mattermost/docker-compose.yml up -d
  Then:      http://[SYSTEM_IP]:8065 → create admin account → create bot → copy token
  Token goes in: ~/nexus/.env  as  MM_BOT_TOKEN=
```

Token is NOT collected now. Added to `.env` manually after Mattermost is running.

**Discord local bot:**
```
~/dockers/discord-bot/
├── docker-compose.yml   ← generated (official discord.py or similar)
└── README.txt           ← instructions: create app at discord.com/developers, get token
```

**Port conflict detection:**  
Before writing any compose file, check if the default port is already in use:
```bash
ss -tlnp | grep :8065
```
If in use: prompt for alternate port.

---

### [I] Service Install

Same as current — `sudo systemctl enable --now nexus`. Already permitted by sudoers rule.

---

### [J] Install Summary

```
  System:        nexus-test-production
  Orchestrator:  chamberlain
  Providers:     anthropic (orchestrator), ollama (failover)
  Adapters:      mattermost (pending token)
  Service:       nexus.service — running
  Log:           ~/Logs/install.log

  Next steps:
  1. Start Mattermost: docker compose -f ~/dockers/mattermost/docker-compose.yml up -d
  2. Visit http://10.0.0.121:8065 → create admin account → create bot → copy token
  3. Add token: echo "MM_BOT_TOKEN=xxxx" >> ~/nexus/.env
  4. Restart: sudo systemctl restart nexus
```

---

### [K] Use Case Selection — Phase 2 (post-LLM)

**Triggered:** After first provider is confirmed live and service is running.

**Method:** Wizard (or a separate `nexus init` command) asks the live LLM to help the user identify and name their workspace. Example questions the LLM reasons through:
- "What will you primarily use this system for?" (hobbyist / homelab / business / science / finance / personal assistant)
- "What's the main project or focus area?"

**Result:** Workspace folder gets a meaningful name and initial structure. Use case stored in `AI_CONTEXT.md` under the system root.

This section is a stub in v2 — the hooks exist but it runs after the installer completes.

---

## Port Registry (Known Conflicts)

Track known ports upfront so the installer can detect conflicts:

| Service | Default Port | Notes |
|---|---|---|
| Mattermost | 8065 | |
| Ollama | 11434 | |
| Nexus API | 9000 | |
| Nexus action server | 9065 | MM button callbacks |
| Discord bot | — | No local port; connects outbound |
| Telegram bot | — | No local port; connects outbound |
| vLLM | 8000 | |
| LM Studio | 1234 | |

---

## Whiptail Fallback

If `whiptail` is not available (non-Debian systems, containers):
- Fall back to numbered list with comma-separated input
- Same data, different UI

Whiptail is added to the `install.sh` system check (installed if missing).

---

## Implementation Notes

- All provider-specific install flows live in `src/setup/providers/` — one file per provider
- All adapter docker-compose templates live in `templates/docker/` — one file per adapter
- Port conflict check is a shared utility in `src/setup/port_check.py`
- System IP detection: `hostname -I | awk '{print $1}'` with fallback to `ip route get 1 | awk '{print $7}'`
- Background Ollama pull uses `subprocess.Popen` with a spinner thread
- Phase 2 hooks: `src/setup/wizard.py` calls `_post_install_reasoning()` stub at the end; no-op until Phase 2 is built
