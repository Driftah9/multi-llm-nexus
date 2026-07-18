# Installer Resilience (PW-6)

This document describes the two-layer installer resilience system:

1. **Preflight validator** — pre-install checks
2. **Checkpoint tracker** — resume-on-failure

> **Testing is local-only.** The installer is interactive (whiptail menus, sudo
> prompts, TTY-bound `read`), which cannot run in a headless CI environment. This
> repo intentionally has **no GitHub Actions / CI**. Validate the installer on a
> real Ubuntu VM or container using the manual steps in *Local Testing* below.

> **Not yet wired into `install.sh` (2026-07-18).** Both tools described here —
> `scripts/preflight_check.sh` and `scripts/install_state.py` — are **standalone tools
> today**. `install.sh` does **not** currently invoke either one; the "Integration with
> install.sh" snippets below are the *intended wiring*, a follow-up, not the current state.
> Run the preflight and checkpoint tools by hand until that wiring lands.

## Layer 1: Preflight Validator

**File:** `scripts/preflight_check.sh`

Validates system readiness before `install.sh` begins. Catches missing resources, network issues, and permissions gaps early.

### Checks Performed

| Check | Required | Optional |
|-------|----------|----------|
| Linux OS | ✓ | — |
| Root privileges | ✓ | — |
| ≥2GB disk space | ✓ | — |
| ≥1GB memory available | — | ⚠️ warn only |
| DNS resolution (github.com) | ✓ (contributes to exit 1) | — |
| GitHub HTTPS reachability | — | ⚠️ warn only |
| Python 3.11+ | ✓ | — |
| git, curl, whiptail | ✓ | — |
| Port availability (53, 8065, 8080) | — | advisory (warn only) |
| Sudo.d writable | — | ⚠️ warn only |

### Usage

```bash
# Run before install.sh
sudo bash scripts/preflight_check.sh

# Auto-fix where possible (marks missing apt packages as "will install
# during setup" instead of failing outright)
sudo bash scripts/preflight_check.sh --fix

# Exit codes:
#   0 = all checks passed
#   1 = checks failed (fix required)
#   2 = some warnings (may proceed)
```

### Integration with install.sh

The preflight validator should be run before `install.sh` starts. Add to the top-level install script:

```bash
echo "Running preflight checks..."
if ! bash scripts/preflight_check.sh; then
    echo "Preflight failed — cannot proceed"
    exit 1
fi
```

## Layer 2: Checkpoint Tracker

**File:** `scripts/install_state.py`

Persists install progress to `~/.nexus-install-state.json`. Tracks which phases have completed, enabling graceful resume if the install fails midway.

### State File Format

```json
{
  "version": 1,
  "started_at": "2026-01-15T10:30:00+00:00",
  "phases": {
    "system-packages": {
      "completed_at": "2026-01-15T10:32:15+00:00",
      "status": "ok"
    },
    "bot-user-creation": {
      "completed_at": "2026-01-15T10:33:45+00:00",
      "status": "ok"
    }
  }
}
```

### Python API

```bash
# Mark a phase as complete
python3 scripts/install_state.py mark-phase PHASE_NAME

# Check if a phase is done (returns exit code 0/1)
python3 scripts/install_state.py is-completed PHASE_NAME

# Display current state
python3 scripts/install_state.py show

# Reset all progress (start fresh)
python3 scripts/install_state.py reset
```

### Integration with install.sh

Wrap major phase blocks with checkpoint markers:

```bash
# At the start of install.sh (root phase)
INSTALL_PYTHON_BIN=$(python3 scripts/install_state.py is-completed system-packages && echo "skip" || echo "run")

if [[ "$INSTALL_PYTHON_BIN" != "skip" ]]; then
    # ... perform system package check ...
    python3 scripts/install_state.py mark-phase system-packages
fi
```

### Phases to Checkpoint

Suggested phase names for `install.sh`:
- `preflight-check` — OS + resources validated
- `system-packages` — Python, git, curl installed
- `bot-user-creation` — bot user account created
- `sudoers-setup` — sudo rules configured
- `venv-setup` — Python virtual environment created
- `repo-clone` — Nexus repo cloned
- `setup-wizard` — Config wizard completed
- `systemd-service` — systemd service installed

## Local Testing

The installer must be tested manually on a real Ubuntu VM or container — it is
interactive and cannot be exercised end-to-end in headless CI. There is no CI
workflow for this; testing is the operator's responsibility.

Recommended matrix to cover before a release:

| OS | Scenario |
|----|----------|
| Ubuntu 22.04 LTS | Fresh install |
| Ubuntu 24.04 LTS | Fresh install |
| Ubuntu 24.04 LTS | Resume from checkpoint |

To run a local test:

```bash
# 1. Create a VM or container
docker run -it --rm ubuntu:24.04 bash

# 2. Clone the repo (inside the container/VM)
git clone https://github.com/Driftah9/multi-llm-nexus.git
cd multi-llm-nexus

# 3. Run preflight
sudo bash scripts/preflight_check.sh

# 4. Run install
sudo bash install.sh

# 5. Watch logs
tail -f ~/Logs/install.log
```

## Design Principles

### Checkpoint Design

- **Idempotent phases:** Each phase is safe to re-run (skipped if already completed)
- **Atomic writes:** State file is written atomically (temp + move)
- **No secrets in state:** Only phase names and timestamps, never API keys or passwords
- **User-accessible:** Bot user can inspect `~/.nexus-install-state.json` directly

### Preflight Design

- **Non-destructive:** Preflight never modifies the system (read-only checks only)
- **Fast:** Should complete in <10 seconds, even with network checks
- **Clear output:** Each check is explicit (pass/warn/fail)
- **Resumable from failure:** Preflight can be re-run anytime

### Local Testing Design

- **Realistic:** Uses the same install script as end users (no mocks)
- **Isolated:** Each run starts from a fresh VM or container
- **Reproducible:** Same OS matrix, same preflight → install → verify steps
- **Operator-run:** No CI; a maintainer runs the matrix before a release

## Future Enhancements

- [ ] Rollback on phase failure (clean up partially-installed state)
- [ ] Dry-run mode (`--dry-run` flag) for testing without making changes
- [ ] Configuration backup/restore (checkpoint .env before wizard)
- [ ] Telemetry (track common failure modes locally)
- [ ] Multi-provider testing (AWS, GCP, Azure VM templates)
