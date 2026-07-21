# Directory Layout — where every file type lives

**What this is.** The canonical on-disk layout a Nexus install materializes, and the rule for
*adding new specialized directories* so they never get scattered per-install. Nexus is
provider-agnostic; the directory layout is part of that contract. If a file type has no
defined home, every operator re-derives one and they all diverge — this doc is the single
source of truth that prevents it.

_Last verified against code: 2026-07-21_ (`install.sh` `ROOT_FOLDERS`, `setup.sh` `VENV_DIR`)

Honest-status anchors: [`BUILDOUT_STATUS.md`](BUILDOUT_STATUS.md) · [`../KNOWN_LIMITATIONS.md`](../KNOWN_LIMITATIONS.md).

---

## The principle (`# NEXUS:PORTABLE`)

1. **One home per file type.** Before creating any file, consult this table. "All monitor
   scripts live in X" — never "this one here, that one there."
2. **A provider harness directory holds shims/symlinks only, never canonical content.** A
   provider dir (`.claude/`, `.gemini/`, …) can be deleted when you drop that provider and
   the brain loses nothing — the real files live in the neutral homes below. Provider-specific
   glue is a shim that points *into* the canon, brought by the provider.
3. **Venvs get one home by what they serve, not who runs them** (see §Venvs).
4. **Nothing lands loose in the home root.** New content goes to the folder matching its
   function *at creation time*.

## Canonical install layout

The installer (`install.sh` → `ROOT_FOLDERS`) scaffolds these under the install user's home.
The Nexus application itself installs under `~/nexus/`.

| Path | Holds |
|---|---|
| `~/nexus/` | The Nexus application (`src/`, `config/`, `templates/`, `.env`) — the product |
| `~/nexus/.venv/` | Nexus's **own** runtime venv (created by the installer; `setup.sh` activates it) |
| `~/venv/<tool>/` | **System-tool venvs** — one subdir per tool. See §Venvs. |
| `~/Tools/` | System tools/engines + shared vendored code (tool SOURCE; its venv lives in `~/venv/`) |
| `~/workspace/` | Projects, one dir each (self-contained: source + its own venv + docs) |
| `~/adapters/` | Platform/provider adapters |
| `~/Agents/` | Agent definitions |
| `~/skills/` | Skills (provider dirs symlink INTO here; never own) |
| `~/Scripts/` | Watchers, monitors, one-shots, utilities |
| `~/Config/` | System configuration |
| `~/Data/` | Operator/app persistent data — cache, registries (organizational drop-zone; core runtime DBs do **not** live here — they're under `~/.local/nexus/`, below) |
| `~/Logs/` | Logs |
| `~/backups/` | Snapshots (pre-change milestones + automated) |
| `~/dockers/` | Docker stacks |
| `~/Inbox/` | Inbound drops awaiting review |
| `~/Temp/` | Temp / working / disposable files (scratch, ephemeral screenshots) |
| `~/research_cache/` | Cached research artifacts |
| `~/src/`, `~/tests/` | Scaffold roots for install-local source/tests |

Identity templates (`SOUL.md`, `AI_CONTEXT.md`, `OPERATING_PROCEDURES.md`) are rendered into
the home root from `~/nexus/templates/system/` at install time.

## Two classes of directory — who creates what

The installer's `ROOT_FOLDERS` is **not** the complete set of directories the running system
uses, and it doesn't need to be. There are two classes:

- **Installer-scaffolded organizational homes** (the table above) — drop-zones nothing else
  creates, where the operator/config places things (`Tools/`, `workspace/`, `venv/`, `skills/`,
  `Config/`, `adapters/`, `Agents/`, `dockers/`, …). These **must** be in `ROOT_FOLDERS`, because
  no code auto-creates them. `venv/` is one of these (added 2026-07-21).
- **Code-self-created runtime state** — every path the engine writes to calls
  `mkdir(parents=True, exist_ok=True)` on first use, so these are created at runtime, not by the
  installer:

  | Path | Created by | Holds |
  |---|---|---|
  | `~/.local/nexus/` | code, on first use | Runtime DBs — RAG store, `skill-metrics.db`, `triage-validation.db`, session/journal/queue/staging/council-session state |
  | `~/.local/etc/nexus.env` | operator (optional) | Secondary secrets override; read if present, **safely skipped if absent** (`main.py:76`). Primary secrets stay in `~/nexus/.env` |

  Rule of thumb: if a directory only ever holds machine-written state, let the code create it
  (don't add it to `ROOT_FOLDERS`). If it's a place a human or config *puts* something, it must
  be scaffolded by the installer.

## Venvs

A venv's home is decided by **what it serves**, not which AI provider is driving:

- **System-tool venvs** → centralize under `~/venv/<tool>/` (one lowercase subdir per tool).
  The tool's SOURCE stays in `~/Tools/<name>/`; only the venv moves to `~/venv/`. This keeps
  bulky `lib/` trees out of source dirs and gives one legible inventory any provider can be
  pointed at.
- **Project venvs** → stay co-located inside the project (`~/workspace/<project>/venv/`). A
  project is a self-contained portable unit; its venv is part of the deliverable.
- **Manager-owned venvs** (pipx, ESP-IDF, and Nexus's own `~/nexus/.venv`) → stay where their
  manager puts them. Relocating breaks the manager.

**Venvs are not relocatable.** Their scripts hardcode absolute paths. After ANY move: rewrite
internal paths (shebangs, `pyvenv.cfg`, activate scripts) *and* every external reference
(launcher scripts, systemd `ExecStart`, MCP server `command`) — then verify the interpreter
boots and the package imports before calling it done.

## Adding a new specialized directory (the anti-scatter rule)

When a new file type needs a home, do all three in the SAME change, or it drifts:

1. Add the folder to `ROOT_FOLDERS` in `install.sh` (so fresh installs scaffold it).
2. Add its row to the table above (+ bump the freshness stamp).
3. Update its entry in [`DOC_INDEX.yml`](DOC_INDEX.yml) so future `install.sh` edits trigger a
   reconcile against this doc.

## Known convergence gaps (honest status)

The live claude-brain install (the port source) has converged to an **all-lowercase** root
(`tools/`, `config/`, `data/`, `tmp/`, `projects/`) with a canonical `Memory/` and `context/`
root. The Nexus installer still scaffolds the historical mixed-case names above
(`Tools`, `Config`, `Data`, `Temp`, `workspace`) and does not yet scaffold dedicated
`Memory/`/`context/` roots. Aligning case/names is a pending live→Nexus convergence item —
tracked in [`../KNOWN_LIMITATIONS.md`](../KNOWN_LIMITATIONS.md); not blind-renamed here because
code paths reference the current names. This doc describes what the installer creates **today**;
the convergence target is the lowercase live canon.
