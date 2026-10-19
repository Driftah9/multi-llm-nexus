#!/usr/bin/env python3
"""PreToolUse hook — workspace guard for the claude_code provider.

Claude-Code-style hook contract: tool-call JSON on stdin (`tool_name`, `tool_input`,
`cwd`), exit 2 + stderr = block (stderr is fed back to the model), exit 0 = allow
(stderr warnings are shown as a heads-up). Same checker the in-process agent loop
uses — see src/core/workspace_guard.py for the office model.

Register in the provider's hook settings, matcher "Bash":
    {"type": "command", "command": "python3 <repo>/scripts/workspace_guard_hook.py"}

The context's linked project is taken from `cwd`: a turn running inside
workspace/<project>/ is contained to that project. NEXUS_WORKSPACE_ROOT overrides
the layout-manifest root (tests / non-standard installs).
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

# Load the module by file path: importing the `src.core` package boots the whole engine
# (seconds), and this hook runs on every shell call.
_spec = importlib.util.spec_from_file_location(
    "_nexus_workspace_guard",
    Path(__file__).resolve().parents[1] / "src" / "core" / "workspace_guard.py")
_wg = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _wg
_spec.loader.exec_module(_wg)
WorkspaceGuard = _wg.WorkspaceGuard


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    if data.get("tool_name") != "Bash":
        return 0
    cmd = (data.get("tool_input") or {}).get("command", "")
    cwd = data.get("cwd") or os.getcwd()
    guard = WorkspaceGuard(os.environ.get("NEXUS_WORKSPACE_ROOT") or None)
    home = cwd if guard.office(cwd) else None
    v = guard.check_command(cmd, cwd=cwd, home=home)
    if not v.allowed:
        sys.stderr.write(v.reason + "\n")
        return 2
    if v.warnings:
        sys.stderr.write("ℹ workspace — these belong to a project (heads-up): "
                         + "; ".join(v.warnings) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
