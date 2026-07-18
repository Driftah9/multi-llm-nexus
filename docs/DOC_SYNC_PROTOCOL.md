# Documentation Sync Protocol — docs are part of the change, not an afterthought

**The rule:** any change to the code, config, or capabilities of Nexus — and *every*
live→Nexus port — begins with a documentation reconciliation step, **before** the docs are
edited and in the **same commit/PR** as the code. Docs are not allowed to lag the code.

_Last verified against code: 2026-07-18_

---

## Why this exists (the drift post-mortem)

On 2026-07-18 a full documentation audit found the docs had silently drifted from the code —
in **both** directions:

- **Overclaimed** aspirations as working: a "self-improvement loop" that was never built;
  "automatically routes around busy pools" for a path that isn't wired; the Nexus Mesh
  presented as active development; hardware "builds" implied as validated.
- **Underclaimed** finished work: `POOL_ROUTING_REFACTOR.md` said *"Planning"* for a feature
  that was fully **implemented**; the provider roadmap said *"Not connected"* for live
  providers; `PHILOSOPHY.md` said the council was *"not implemented"* when it was ported.

The cause was always the same: **code shipped, docs didn't move with it.** Nobody checked
which docs made claims about the changed subsystem, so the claims quietly went stale. This
protocol makes that check a required first step so it can't be skipped again.

The honest-status anchors that must never drift: [`BUILDOUT_STATUS.md`](BUILDOUT_STATUS.md)
and [`../KNOWN_LIMITATIONS.md`](../KNOWN_LIMITATIONS.md). Everything else points to them.

---

## Step 1 — Reconcile, before you edit anything

Before writing a line of documentation (and as the opening move of any live→Nexus port),
list every doc that makes a claim about what you changed:

```bash
# what a PR against main would change:
python scripts/doc_check.py --diff origin/main

# or your working changes / a specific file:
python scripts/doc_check.py --diff
python scripts/doc_check.py --staged
python scripts/doc_check.py src/core/pool_manager.py
```

`doc_check.py` finds affected docs two ways: **direct mention** (a doc names the changed
module) and the **claim map** in [`DOC_INDEX.yml`](DOC_INDEX.yml) (a doc makes a conceptual
claim about the subsystem without naming the file — e.g. "self-improvement loop"). It prints
the docs to review and the *current claim that must stay true* for each subsystem.

This is advisory, not a gate that blocks you — but skipping it is how drift returned last time.

## Step 2 — Read each listed doc against reality

For every doc the tool surfaced, check the claim against what your change actually did:

- Did a feature move from designed → built? (kill the "not built" / "planned" language)
- Did a feature move from stubbed → wired, or the reverse? (fix the status)
- Did counts, ports, model names, file paths, or defaults change? (update them)
- Did a *concept* become real code? (flip the "concept, not built" banners — this is a big one)

**Label, don't delete.** Vision and roadmaps stay — they get an accurate status stamp. The
goal is not fewer claims, it's *true* claims.

## Step 3 — Edit the docs in the SAME commit as the code

The doc fix ships with the code change, not in a "docs later" follow-up that never comes.
Update each touched doc's freshness stamp:

```
Last verified against code: YYYY-MM-DD
```

If a subsystem changed shape (new module, renamed file, new capability), update its entry in
[`DOC_INDEX.yml`](DOC_INDEX.yml) in the same change too — the map is only useful if it's current.

## Step 4 — If nothing changed for readers, say so

Not every code change needs a doc edit. If `doc_check` lists docs but none of their claims
became false, that's a valid outcome — the tool is advisory. The discipline is that you
**looked**, not that you always edit.

---

## For live→Nexus ports specifically

Nexus is the OSS port target of the live claude-brain stack; convergence runs **live → Nexus**.
Because a port lands a batch of behavior at once, it's the highest-risk moment for drift. The
port isn't done when the code + tests are green — it's done when `doc_check.py --diff` comes
back reconciled and the freshness stamps are current. Treat the doc pass as the final,
non-optional step of the port, the same way tests are.

## Freshness-stamp convention

Any doc that describes system state carries, near the top:

```
Last verified against code: YYYY-MM-DD
```

A stamp older than the code it describes is a signal to re-run Step 1 against it. The two
anchor docs (`BUILDOUT_STATUS.md`, `KNOWN_LIMITATIONS.md`) already follow this; extend it to
any doc as you touch it.
