# Installer Resilience (PW-6)

This document describes the three-layer installer resilience system:

1. **Preflight validator** — pre-install checks
2. **Checkpoint tracker** — resume-on-failure
3. **CI matrix** — test matrix for multiple OS versions

## Layer 1: Preflight Validator

**File:** `scripts/preflight_check.sh`

Validates system readiness before `install.sh` begins. Catches missing resources, network issues, and permissions gaps early.

### Checks Performed

| Check | Required | Optional |
|-------|----------|----------|
| Linux OS | ✓ | — |
| Root privileges | ✓ | — |
| ≥2GB disk space | ✓ | — |
| Network (DNS + GitHub) | — | ⚠️ warn only |
| Python 3.11+ | ✓ | — |
| git, curl, whiptail | ✓ | — |
| Sudo.d writable | — | ⚠️ warn only |

### Usage

```bash
# Run before install.sh
sudo bash scripts/preflight_check.sh

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

## Layer 3: CI Matrix

**File:** `.github/workflows/install-test.yml`

Automated installer testing across OS versions and scenarios.

### Test Matrix

| OS | Scenario |
|----|----------|
| Ubuntu 22.04 LTS | Fresh install |
| Ubuntu 24.04 LTS | Fresh install |
| Ubuntu 24.04 LTS | Resume from checkpoint |

### GitHub Actions Workflow

**Trigger:** Commits to `main`, pull requests, or manual dispatch via `workflow_dispatch`

**Steps per job:**
1. Run `preflight_check.sh` (non-fatal warnings)
2. Run `install.sh` with mocked interactive inputs
3. Verify install output (`/home/nexus-ci/nexus/`)
4. Check Python venv, config files, systemd service

### Running Locally

To test the installer locally before pushing:

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

### CI Design

- **Quick feedback:** Tests complete in <30 minutes
- **Isolated:** Each test run is independent (fresh VM per job)
- **Realistic:** Uses the same install script as end users
- **Extensible:** Easy to add new OS versions or scenarios

## Future Enhancements

- [ ] Rollback on phase failure (clean up partially-installed state)
- [ ] Dry-run mode (`--dry-run` flag) for testing without making changes
- [ ] Configuration backup/restore (checkpoint .env before wizard)
- [ ] Telemetry (track common failure modes, report to CI dashboard)
- [ ] Multi-provider testing (AWS, GCP, Azure VM templates)
