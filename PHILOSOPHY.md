# Nexus Development Philosophy

> **Why this exists:** Nexus is not developed by implementing features. It's developed by validating ideas in isolation, extracting the behaviors that consistently survive real-world use, removing their project-specific assumptions, and only then promoting those generalized behaviors into the core platform.

---

## The Core Loop

Nexus operates a **validate → extract → generalize → promote** cycle:

### 1. Validate in Isolation
An idea is tested in a real deployment against actual workloads before it enters core. This could be:
- A provider failover strategy tested under quota/auth failures in production
- A memory injection layer tested with different models to see which ones benefit
- A routing gate tested across hardware tiers (CPU-only, one GPU, distributed)

**Why:** Many patterns that look solid on a whiteboard fail in production. By testing first, we avoid shipping half-solutions.

### 2. Extract Surviving Behaviors
Once validated, we extract the **minimum behavioral pattern** that made it work — not the implementation details, not the project-specific setup, just the pattern itself.

**Example:** The capability-gate mechanism in `core/capability_gate.py` came from observing that certain features only activate when specific constraints are met (enough executors, shared state available, RAM threshold). The extracted pattern: *a feature declares what it needs, the system snapshots what's available, and decides active/deferred without special-casing each combination.*

### 3. Strip Project-Specific Assumptions
The validated behavior is generalized by removing operator-specific, hardware-specific, and provider-specific assumptions.

**Examples already done:**
- **Provider roster:** The live deployment hardcodes specific cloud providers. Nexus has zero hardcoded provider names — operators wire their own via `config/providers.yaml`.
- **Hardware tiers:** The live deployment runs on specific GPUs and VMs. Nexus detects available hardware and scales model recommendations up/down without code changes.
- **Communication channels:** The live deployment uses a specific internal chat platform. Nexus speaks Mattermost, Discord, Telegram, and the adapter interface is channel-agnostic.
- **Memory storage:** The live deployment uses a specific database. Nexus has a pluggable `memory_injector` contract — swap the backend without touching provider-facing code.

### 4. Promote to Core
Once generalized, the behavior becomes core platform infrastructure, available to any operator without custom code.

**Examples already promoted:**
- **Multi-provider failover** (`core/provider_chain`, `core/error_classifier`) — tested live, extracted the state machine, generalized error classification (transient/quota/auth/bad_request), and promoted as a routing primitive every task uses.
- **Structured-output safety** (`core/schema_gate`) — tested live, extracted the pattern "schema validation should fail-open and let the caller decide retry strategy," promoted as a gate all structured-output tasks pass through.
- **Hardware-adaptive routing** (`src/setup/hardware_detect.py`) — tested live, extracted "model recommendation should be purely a function of detected resources," promoted as the wizard's hardware-sensing phase.

---

## Why This Approach

**Avoids premature generalization:** A pattern that works in isolation might fail at scale, under different providers, or with different hardware. By validating first, we ship solutions that actually hold up.

**Keeps core lean:** Only ideas that have proven themselves in the real world enter core. This makes the codebase smaller, the upgrade path safer, and debugging easier.

**Makes onboarding possible:** An operator can deploy Nexus to radically different hardware and workloads — a tiny CPU box, a GPU cluster, a cloud-only setup — and the same codebase adapts because the core is patterns, not assumptions.

---

## Known Gaps (Honest Assessment)

### Incomplete Validation
- **Structured-output robustness under load:** The schema-gate works, but it's been tested against one class of models (Claude, GPT). Smaller models or specialized providers might expose edge cases we haven't seen yet.
- **Multi-orchestrator failover at scale:** The lease + fencing mechanism is sound in theory and tested in simulation, but hasn't run in a 10+ orchestrator deployment under actual failures. The mechanism may need adjustment based on real-world cluster behavior.
- **Memory injection across provider personality shifts:** A model swap mid-session works, but we haven't validated whether memory quality (relevance, density) degrades gracefully when switching from a large model to a small one.

### Deferred Decisions
- **Learned quality scoring:** We classify provider errors and route by cost, but *learned quality scores* (this provider is faster / returns better JSON / has higher accuracy on math) are collected but not yet wired into routing. This is a significant gap for operators who want quality optimization over cost.
- **Cross-provider adversarial council:** A fixed-role council (Skeptic / Advocate / Verifier) is now ported and tested, but it is **inert by default** — not wired into the standard reply path (behind `SWARM_LOOP_ENABLED=0`). Independent multi-orchestrator debate *at scale* — orchestrators challenging each other and self-correcting across a live cluster — remains unvalidated.
- **Operator-specific budget policies:** Tasks can be routed by cost, but there's no declarative way to express "spend up to $X per day" or "never hit the expensive model unless quality is explicitly low." Operators implementing this today roll it themselves.

### Known Limitations (By Design)
- **Not optimized for interactive chat:** Nexus is optimized for long-running, asynchronous tasks (research, code review, data processing). Interactive back-and-forth conversation works, but latency is higher than a cloud-only service like ChatGPT because of local-first routing. If sub-200ms latency is critical, a cloud-first service is a better fit.
- **Not a replacement for specialized models:** If you need a domain-specific fine-tuned model (domain-adapted law, medical coding, customer-specific language), Nexus routes to what you have, but it doesn't fine-tune. You'll need to bring your own specialized model and register it as a provider.
- **Not a chat interface replacement:** Nexus is an agent platform, not a ChatGPT-like interface. It has no conversation persistence layer — each session is a task. If you need persistent multi-turn chat history, build it on top using Nexus as the LLM router.

---

## How It Compares

### vs. LangChain / LlamaIndex
**LangChain** and **LlamaIndex** are libraries for building agentic workflows. Nexus is a deployed platform that *uses* such libraries internally. The differences:
- **Scope:** LangChain is "how do I build this agent?" Nexus is "here's a running agent, with multi-provider routing, memory, and failover already built."
- **Operations:** LangChain gives you components; Nexus gives you infrastructure. You deploy Nexus once, configure it, and it runs. You integrate LangChain into your code.
- **Provider agnosticism:** Both support multiple providers, but LangChain defaults to OpenAI; Nexus is 100% provider-agnostic by design. Neither is "better" — LangChain is faster if you're building a prototype with OpenAI, Nexus is lower-lock-in if you want to stay independent.

### vs. n8n / Zapier
**n8n** and **Zapier** are workflow automation platforms. Nexus is an LLM orchestration platform. The differences:
- **LLM-first:** Nexus is built around routing intelligence across models. n8n treats AI as one node in a workflow. For AI-heavy workloads, Nexus has better semantics; for business process automation, n8n is more mature.
- **Deployment:** n8n is a GUI-first platform; Nexus is config-first. n8n is easier for non-programmers; Nexus is faster for infrastructure-fluent teams.
- **Provider coverage:** n8n integrates with 500+ apps. Nexus fronts 22 selectable LLM providers (100+ models). These are different problems solved by different tooling.

### vs. Anthropic's Claude API + System Prompts
**The Claude API** is the simplest choice if you're building an AI feature in your app. Nexus adds value when you:
- Want multi-provider routing (Claude fails → fallback to Mistral)
- Need local-first deployment (no cloud calls for simple tasks)
- Run task-heavy workloads where cost/latency/quality trade-offs matter
- Want structured orchestration across many AI tasks with different roles

If you just need "good responses from a single model," the Claude API is simpler and cheaper. Nexus is overhead for that use case.

### vs. Specialized Agents (AutoGen, Crewai)
**AutoGen** and **Crewai** are frameworks for multi-agent systems. Nexus includes agent infrastructure but is built around the *operator* role — you define the rules, the platform executes them. The differences:
- **Philosophy:** AutoGen is "agents decide what to do"; Nexus is "operator decides, platform ensures it happens reliably."
- **Scope:** AutoGen focuses on agent-to-agent communication; Nexus focuses on operator-to-platform communication and multi-provider resilience.
- **Maturity:** AutoGen has more published research and examples. Nexus is younger and more opinionated about deployment.

They're complementary — you could run an AutoGen-style multi-agent system *inside* Nexus as a specialist.

---

## How to Read This Codebase

If you're adopting Nexus or contributing to it, the validate → extract → generalize → promote pattern is everywhere:

1. **Validation layer:** Look in `docs/convergence-2026-06.md` and `IMPLEMENTATION_SUMMARY.md` for the decision breadcrumbs — why a pattern was tested, what it was tested against, and what failed or succeeded.

2. **Extracted patterns:** Look in `src/core/` — each module like `capability_gate.py`, `error_classifier.py`, `schema_gate.py` is an extracted pattern that survived real-world use.

3. **Generalization:** Look at the module's contract (its public interface, what it accepts, what it returns). Minimal assumptions about provider/hardware/operator-specific details. Everything is configurable.

4. **Promotion:** Look at how the pattern is used in `Bridge.invoke()`, routing pipelines, and the CLI. If a pattern is in core, it's available to every operator without custom code.

---

## Contributing

When proposing a new feature to Nexus:

1. **Validate it first** — test it in your own deployment or a test deployment. Don't propose a pattern you haven't seen work in practice.

2. **Extract the minimum behavioral essence** — what's the simplest pattern that made it work? Remove project-specific details.

3. **Demonstrate it works across contexts** — show it works with different providers, different hardware, different operator configurations.

4. **Promote it thoughtfully** — where does this live in core? Is it a gate, a routing primitive, a CLI command? How does it fit into the existing patterns?

If you're proposing something that conflicts with "provider-agnostic" or "operator-first," be explicit about why that's necessary. Most features can be generalized; the question is whether the cost of generalization is worth the benefit.

---

## Open Questions

- **Quality scoring:** Should operators be able to declare "this provider is 15% more accurate on math"? How is that learned, tested, and validated without manual benchmarking?
- **Budget policies:** Should "spend no more than $X per category" be a first-class feature or operator-implemented?
- **Orchestrator debate:** Can independent orchestrators challenge each other's decisions and self-correct? Under what consistency model?

These are not show-stoppers — Nexus works without them — but they're the next frontier for refinement.
