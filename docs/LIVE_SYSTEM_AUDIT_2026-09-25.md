# Live System Audit — 2026-09-25

Audit of the running claude-brain install against this repo, to decide what still needs
porting and to correct stale claims in `AGENTS.md` / `docs/BUILDOUT_STATUS.md`.

Everything below was verified by direct inspection of the live install (filesystem, crontab,
service state, job logs, and job output JSON). Items that could not be verified are marked
**UNVERIFIED** and should not be treated as fact.

---

## 1. Headline correction: model auto-discovery exists live, not here

A prior session concluded "there is no auto-discovery of new models." That is **wrong for the
live install** and **right only for Nexus**. The live system runs a four-stage discovery and
grading chain on cron, and it ran successfully this morning.

| Time (ET) | Driver | What it does | Output | Verified |
|---|---|---|---|---|
| 06:00 daily | `scripts/discover_provider_limits.py` | per-provider quota/limit discovery | `logs/discover-limits.log` | ✅ in crontab |
| 06:05 daily | `scripts/discover_models.py` | new-model diff across providers | `data/model_discovery.json` (`checked_at`, `providers`, `claude_family`) | ✅ in crontab |
| 06:20 daily | `scripts/research-capabilities.sh` → `orchestration/capability_research_job.py` | benchmark-scans models **new since last run**, updates capability pre-seed scores, flags conflicts with earned scores | `data/adapters/model_intel.json` (14 model entries, 948 KB of evidence text) | ✅ ran 2026-09-25 06:44, exit 0, "added 14/15 new model(s)" |
| 08:10 Sundays | `scripts/llmfit_fit_check.py` | hardware-fit rescan of the local floor model | `data/llmfit_fit_check.json` | ✅ ran 2026-09-20, no qualifying candidate |

Ordering is load-bearing: the research job reads the *same morning's* fresh diff from
`discover_models.py`. Since 2026-09-08 it is event-gated — a morning with no new models exits fast
instead of burning Claude calls.

**Nexus has none of this.** `grep` for `model_intel`, `access_terms`, `capability_baseline`,
`benchmark_poller` across this repo returns zero hits. Nexus's model story stops at
`src/lifecycle/manager.py` (tracks updates to models you already declared) plus
`src/lifecycle/fit_check.py` (the llmfit wrapper). The discovery front-end and the
benchmark→capability-map feedback loop are the missing half.

### Built live but never scheduled — port with caution
Two live modules are written and documented but have **no cron entry, no log file, and no output
file**, so they have never run:

- `orchestration/capability_benchmark_poller.py` (351 lines) — monthly polling of lm-eval-harness,
  Chatbot Arena, HumanEval; a driver `scripts/capability-benchmark-poller.sh` exists but is
  unscheduled.
- `orchestration/access_terms_refresh.py` (198 lines) — monthly pricing / free-tier / quota refresh;
  its `access_terms.json` output does not exist on disk.

These are unproven code. Wiring them up live is the cheaper next step than porting them blind.

---

## 2. Portable-module gap: 54 live modules tagged `NEXUS:PORTABLE` are absent here

90 live files carry a `# NEXUS:PORTABLE` stamp — the live author's own marker that the mechanism
is provider-agnostic and belongs in Nexus. Of those, **54 have no counterpart** under
`src/core/` or `src/orchestration/`.

**Model / provider lifecycle** (the cluster behind §1)
`capability_baseline.py` · `capability_research_job.py` · `access_terms_refresh.py` ·
`model_lifecycle.py` · `provider_catalog.py` · `provider_registry.py` · `provider_resolver.py` ·
`provider_manifest.py` · `provider_blocklist.py` · `provider_health.py` · `provider_status.py` ·
`provider_onboard_hook.py`

**Swarm reliability** — `worker_health.py` is the standout. Live measured 2026-09-04: a 10-provider
worker pool with 1 reachable provider; two dead top-ranked providers consumed the entire retry
budget and swarm step completion decayed 100% → 0/11 with no user-visible error, because quality
EWMA only moves on success so a provider that fails 100% of the time keeps its earned rank forever.
Nexus has the same `capability_map` ranking and the same `MAX_STEP_RETRIES` shape, so **Nexus has
this bug today** — it is just masked by `SWARM_LOOP_ENABLED=0`. This is the highest-priority port.

**Emergent domains** — `domain_classifier.py` (category-only labels, structurally enforced against
leaking client/brand/proper nouns) · `domain_registry.py`

**Seat / failover** — `primary_lock.py` · `seat_lease.py` · `failover_executor.py` ·
`recovery_watcher.py` · `orchestrator_advisory.py` · `council_lease.py`-adjacent checkpointing

**Agent loop & guards** — `agent_bridge.py` · `agent_guards.py` · `agent_tools.py` ·
`turn_pipeline.py` · `contract.py` · `authority.py` · `access.py` · `data_scope.py`

**Memory / state** — `engram_client.py` · `engram_writer.py` · `graphiti_client.py` ·
`memory_scope.py` · `conversation_ledger.py` · `history.py` · `checkpoint.py` · `identity_store.py` ·
`standing_profile.py` · `profile_detector.py`

**Skills subsystem (entirely absent from Nexus)** — `skill_manifest.py` · `skill_roster.py` ·
`skill_router.py` · `skill_usage.py` · `skill_gap_detector.py`

**Infra** — `paths.py` (canon path resolution) · `base_bridge.py` · `adapter_base.py` ·
`claude_bridge.py` · `context_budget.py` · `formatting.py` · `placement_notifier.py` · `scribe.py`

Live also carries `orchestration/governance/` (`CONSTITUTION.md` + `policy/`) and
`orchestration/watcher/` (the local-LLM watcher service template + heartbeat) — the latter is
exactly the "LLM Watcher integration" still listed under *What's Next*.

Note: `orchestration/orchestrator.py` and `brain_agent.py` show as missing only because Nexus put
the orchestrator at `src/core/orchestrator.py`. Not a real gap.

---

## 3. llmfit is no longer a black box — and its verdicts should not be trusted

The prior session parked llmfit as "unknown: frozen or dynamic?". That question was answered
live on 2026-08-14 by measurement against an RX 480, recorded in
`Memory/reference_llmfit_predicts_wrong_axis.md`. **Use llmfit as a candidate generator; do not
use its fit verdicts.**

Where it was right: ranked Qwen2.5-Coder-7B #1 at 8k/16k/24k (matching independent KV-cache
arithmetic), correctly dropped the 7B at 32k where CPU spill was measured, generation estimate
31.2 predicted vs 27.5 measured (~12% optimistic), and surfaced Qwen3-4B — a candidate never
otherwise considered.

Where it fails, and it fails **confidently with a green label**, not with an error:

| @ 24k ctx | llmfit | measured |
|---|---|---|
| memory required | 5.62 GB | **7.3 GB** |
| utilization | 70.3% | **89%** |
| verdict | 🟢 "Perfect" | **3.5× throughput collapse** |

Three structural causes, all of which generalize to any hardware-fit tool:
1. It models **generation only, never prompt-eval** — the binding constraint for long-prompt work.
2. It **omits compute/graph buffers** from memory estimates (~0.7–1.7 GiB), which is exactly what
   turns "70% utilized, Perfect" into a cliff.
3. It infers a **runtime path the hardware cannot execute** (recommended vLLM/AWQ/GPTQ on ROCm;
   ROCm 6.x dropped gfx803 and vLLM does not support the card), and reported `installed: true`
   for an AWQ build when only an ollama Q4_K_M exists.

Its JSON schema has `measured_tps` and `estimate_basis.local_calibration`, both `null`
(`method: "backend_constant"`, `efficiency: 0.55`). Feeding real sweep numbers back would make its
verdicts trustworthy for a specific card. Not done.

**Implication for `src/lifecycle/fit_check.py`:** treating an llmfit "fits / Perfect" verdict as a
go/no-go gate is unsafe. It should propose candidates for measurement, never auto-promote on a fit
score. The Phase 2 HuggingFace→Ollama name mapping is still worth building — llmfit emits HF ids
(`Qwen/Qwen2.5-3B-Instruct`) and the live output already carries a hand-mapped `ollama_name`
(`qwen2.5:3b`), confirming the mapping is needed and not automatic.

---

## 4. Local inference environment (as measured, not assumed)

- **No GPU visible to this VM** — `nvidia-smi` fails (no driver). The RX 480 measurements above
  come from other hardware in the operator's fleet, not from this box. Any fit check run here is
  scoring a CPU-only host.
- **Ollama installed** at `/usr/local/bin/ollama`, 10 models present:
  `granite3.1-dense:8b`, `mistral:7b`, `phi4-mini`, `gemma2:9b`, `qwen3:4b`, `tinyllama`,
  `llama3.1:8b`, `qwen2.5-coder:7b`, `qwen2.5:3b`, `nomic-embed-text`.
  All pulled ≥5 weeks ago (`qwen2.5:3b` ~3 months, `nomic-embed-text` ~4 months).
- The installed roster is **generation-1-behind in places** (gemma2 not gemma3, llama3.1 not 3.3).
  Whether newer tags are worth pulling is a measurement question, not a fit-score question — see §3.
- `llmfit` is installed at `~/.local/bin/llmfit` (not on the default `PATH` for non-login shells,
  which is worth noting for any subprocess wrapper that assumes a bare `llmfit` invocation resolves).

---

## 5. Repo state

- Test suite: **242 passed** (`python3 -m pytest -q`, 12.06s). Clean.
- Working tree: only `.workspace-tools.yml` modified (usage counters, churn).
- `experiment/llmfit-hardware-fit` is **fully merged into `main`** — zero commits unique to the
  branch, and `main` is 3 commits ahead. The llmfit work landed in `45b47fb`. The branch (local and
  `origin/`) is dead weight and can be pruned.

---

## 6. Recommended order of work

1. **Port `worker_health.py`** + its test. Closes a measured live failure mode that Nexus shares,
   and is safe to land while the swarm stays flag-off.
2. **Port the discovery chain** — `discover_models`-equivalent → `capability_research_job` →
   `model_intel` feedback into `capability_map`, plus `capability_baseline.py` as the t=0 prior.
   This is the actual answer to "how do new models get in," and it is the largest single gap.
3. **Downgrade `fit_check.py` to advisory** — propose candidates, never gate on an llmfit verdict.
   Ship the HF→Ollama name map with it.
4. **Wire the two unscheduled live jobs** (`capability_benchmark_poller`, `access_terms_refresh`)
   on the live box first and let them prove out for a cycle before porting.
5. **Port `domain_classifier.py` + `domain_registry.py`** so grades accrue under domains the
   operator actually works in rather than the coarse seed set.
6. Then the skills subsystem and the seat/lease cluster, which are larger and less urgent.
