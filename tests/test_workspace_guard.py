"""Workspace guard (src/core/workspace_guard.py) — the office model.

Case matrix ported from the live claude-brain node_map_guard validation (2026-10-19).
"""
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.core.agent_loop import run_agent
from src.core.workspace_guard import WorkspaceGuard, guard_tool_call
from src.providers.base import BaseProvider, ProviderResponse, ToolCall
from src.tools.definitions import ToolDef, ToolRegistry

REPO = Path(__file__).resolve().parents[1]
RM = "r" + "m"   # keep literal destructive commands out of this file's own text


@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "workspace"
    p = root / "obs"
    for d in ("src/brain", "docs/screens", "assets", ".claude", ".git"):
        (p / d).mkdir(parents=True)
    for f in ("AGENTS.md", "CLAUDE.md", "README.md", ".env", "wp-wall.png", "wp-bench.png",
              "src/brain/tmp.py"):
        (p / f).write_text("x")
    (root / "other").mkdir()
    (root / "other" / "AGENTS.md").write_text("x")
    (root / "umbrella" / "sub-a" / "deep").mkdir(parents=True)
    (root / "umbrella" / "sub-b").mkdir(parents=True)
    (root / "umbrella" / "AGENTS.md").write_text("x")
    (root / "manifested").mkdir()
    (root / "manifested" / ".workspace-tools.yml").write_text(
        "project: manifested\nprotected_paths:\n- data/keep/\n- notes.md\n")
    return root


def _ok(ws, cwd, cmd, linked=True):
    g = WorkspaceGuard(ws)
    cwd = str(cwd)
    home = cwd if linked and g.office(cwd) else None
    return g.check_command(cmd, cwd=cwd, home=home).allowed


ALLOW = [
    # (cwd relative to ws or None for unlinked, command template)
    (None, f"cd {{P}} && {RM} wp-wall.png"),
    (None, f"{RM} {{P}}/wp-wall.png"),
    ("obs", f"{RM} -rf .playwright-mcp wp-*.png"),
    ("obs", f"{RM} src/brain/tmp.py"),
    ("obs", f"{RM} -rf docs/screens"),
    ("obs", f"ls -A | grep -vxE 'AGENTS.md|CLAUDE.md|README.md|src|docs|\\.claude|\\.git|\\.env' | xargs {RM} -rf"),
    (None, f"{RM} {{W}}/other/scratch.txt"),           # unlinked → not contained
    (None, f"find {{P}}/assets -name '*.pyc' | xargs {RM}"),
    (None, "ls {P}"),
    ("umbrella/sub-a", f"{RM} {{W}}/umbrella/sub-b/notes.md"),   # sibling sub-room
    ("umbrella/sub-a/deep", f"cd {{W}}/umbrella && {RM} -rf scratch"),
    ("manifested", f"{RM} -rf data/tmp"),
]

BLOCK = [
    ("obs", f"{RM} AGENTS.md"),
    ("obs", f"{RM} -rf src"),
    ("obs", f"{RM} -rf docs"),
    ("obs", f"{RM} .env"),
    ("obs", f"{RM} -rf .claude"),
    ("obs", f"{RM} -rf .git"),
    ("obs", f"{RM} -rf *"),
    ("obs", f"{RM} -rf ."),
    ("obs", f"{RM} {{W}}/other/scratch.txt"),           # linked → contained
    ("obs", f"cd {{W}}/other && {RM} scratch.txt"),
    (None, f"{RM} -rf {{P}}"),
    (None, f"ls {{P}} && {RM} {{P}}/README.md"),
    (None, f"find {{P}} -name '*.pyc' | xargs {RM}"),
    (None, f"{RM} -rf {{W}}"),                          # the workspace root itself
    ("umbrella/sub-a", f"{RM} {{P}}/wp-wall.png"),       # other top-level project
    ("umbrella/sub-a", f"{RM} -rf {{W}}/umbrella"),
    ("umbrella/sub-a", f"{RM} {{W}}/umbrella/AGENTS.md"),
    ("manifested", f"{RM} -rf data/keep"),               # manifest protected_paths
    ("manifested", f"{RM} -rf data"),                    # parent of a protected entry
    ("obs", "echo hi > AGENTS.md"),                      # overwrite redirect
]


def _fmt(ws, cwd, tmpl):
    c = ws / cwd if cwd else ws.parent
    return c, tmpl.format(P=ws / "obs", W=ws)


@pytest.mark.parametrize("cwd,tmpl", ALLOW)
def test_allowed(ws, cwd, tmpl):
    c, cmd = _fmt(ws, cwd, tmpl)
    assert _ok(ws, c, cmd), cmd


@pytest.mark.parametrize("cwd,tmpl", BLOCK)
def test_blocked(ws, cwd, tmpl):
    c, cmd = _fmt(ws, cwd, tmpl)
    assert not _ok(ws, c, cmd), cmd


def test_non_destructive_commands_pass_untouched(ws):
    g = WorkspaceGuard(ws)
    v = g.check_command(f"cat {ws}/obs/AGENTS.md && ls {ws}/other", cwd=str(ws / "obs"),
                        home=str(ws / "obs"))
    assert v.allowed and not v.warnings


def test_paths_outside_workspace_are_ignored(ws, tmp_path):
    g = WorkspaceGuard(ws)
    assert g.check_command(f"{RM} -rf {tmp_path}/elsewhere", cwd=str(tmp_path)).allowed


def test_move_only_warns(ws):
    g = WorkspaceGuard(ws)
    v = g.check_command("mv AGENTS.md AGENTS.bak", cwd=str(ws / "obs"), home=str(ws / "obs"))
    assert v.allowed and v.warnings


def test_guard_tool_call_maps_command_and_delete_tools(ws):
    g = WorkspaceGuard(ws)
    home = str(ws / "obs")
    assert not guard_tool_call("bash", {"command": f"{RM} AGENTS.md"}, home=home, guard=g).allowed
    assert guard_tool_call("bash", {"command": f"{RM} wp-wall.png"}, home=home, guard=g).allowed
    assert not guard_tool_call("delete_file", {"path": "README.md"}, home=home, guard=g).allowed
    assert guard_tool_call("delete_file", {"path": "wp-bench.png"}, home=home, guard=g).allowed
    assert not guard_tool_call("delete_file", {"path": str(ws / "other" / "x")},
                               home=home, guard=g).allowed
    # a write tool is not destructive in the office model (scaffolding is editable)
    assert guard_tool_call("write_file", {"path": "AGENTS.md"}, home=home, guard=g).allowed


def test_workspace_root_resolves_from_layout_manifest():
    from src.core import layout
    assert WorkspaceGuard().root == os.path.normpath(str(layout.path("workspace")))


# ── agent loop wiring ────────────────────────────────────────────────────────

class _Scripted(BaseProvider):
    def __init__(self, script):
        super().__init__({"model": "fake"})
        self.script = list(script)

    async def send(self, messages, system="", tools=None):
        return self.script.pop(0) if self.script else ProviderResponse(content="done")

    def supports_tools(self):
        return True

    async def health_check(self):
        return True


def _shell_registry(log):
    reg = ToolRegistry()

    async def bash(command: str = "", **kw):
        log.append(command)
        return {"ok": True}

    reg.register(ToolDef(name="bash", description="run a shell command",
                         parameters={"type": "object",
                                     "properties": {"command": {"type": "string"}}},
                         execute=bash))
    return reg


def test_agent_loop_guard_blocks_and_feeds_reason_back(ws):
    log = []
    g = WorkspaceGuard(ws)
    home = str(ws / "obs")

    async def guard_fn(name, args):
        v = guard_tool_call(name, args, home=home, guard=g)
        return v.allowed, v.reason

    provider = _Scripted([
        ProviderResponse(content="", tool_calls=[
            ToolCall(name="bash", arguments={"command": f"{RM} AGENTS.md"}, call_id="a"),
            ToolCall(name="bash", arguments={"command": f"{RM} wp-wall.png"}, call_id="b")]),
        ProviderResponse(content="cleaned up"),
    ])
    r = asyncio.run(run_agent(provider, "clean up", registry=_shell_registry(log),
                              guard_fn=guard_fn))
    assert r.text == "cleaned up"
    assert log == [f"{RM} wp-wall.png"]                     # only the allowed call ran
    assert r.tool_events[0].get("blocked") and not r.tool_events[1].get("blocked")


def test_agent_loop_crashing_guard_fails_closed():
    log = []

    async def guard_fn(name, args):
        raise RuntimeError("boom")

    provider = _Scripted([
        ProviderResponse(content="", tool_calls=[
            ToolCall(name="bash", arguments={"command": "ls"}, call_id="a")]),
        ProviderResponse(content="ok"),
    ])
    r = asyncio.run(run_agent(provider, "x", registry=_shell_registry(log), guard_fn=guard_fn))
    assert log == [] and r.tool_events[0].get("blocked")


# ── hook entrypoint (claude_code provider) ───────────────────────────────────

def _hook(ws, cwd, cmd):
    env = dict(os.environ, NEXUS_WORKSPACE_ROOT=str(ws))
    return subprocess.run(
        [sys.executable, str(REPO / "scripts" / "workspace_guard_hook.py")],
        input=json.dumps({"tool_name": "Bash", "cwd": str(cwd), "tool_input": {"command": cmd}}),
        capture_output=True, text=True, env=env)


def test_hook_blocks_with_exit_2_and_reason(ws):
    r = _hook(ws, ws / "obs", f"{RM} -rf src")
    assert r.returncode == 2 and "workspace guard" in r.stderr


def test_hook_allows_office_work_and_contains_linked_context(ws):
    assert _hook(ws, ws / "obs", f"cd {ws}/obs && {RM} wp-wall.png").returncode == 0
    assert _hook(ws, ws / "obs", f"{RM} {ws}/other/x.txt").returncode == 2
    assert _hook(ws, ws.parent, f"{RM} {ws}/other/x.txt").returncode == 0


def test_hook_ignores_non_bash_tools(ws):
    env = dict(os.environ, NEXUS_WORKSPACE_ROOT=str(ws))
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "workspace_guard_hook.py")],
                       input=json.dumps({"tool_name": "Write", "tool_input": {}}),
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0
