"""Workspace guard — the office model for destructive tool calls.

Ported from the live claude-brain `node_map_guard.py` (2026-10-19), made install-agnostic.

The operator's ``workspace/`` folder holds one directory per project. A context (channel,
topic, room) that is LINKED to a project works inside that project's top-level folder — its
"office". Inside the office the agent may add, edit and delete anything EXCEPT:

  * the office root itself,
  * scaffolding / operational entries (``AGENTS.md``, ``CLAUDE.md``, ``README.md``, ``src/``,
    ``docs/``, ``.git/``, ``.env``, provider dot-dirs, plus any ``protected_paths`` the project's
    optional ``.workspace-tools.yml`` declares) — the entry itself, not its contents.

Nested projects are sub-rooms of the top-level office: containment is always by the top-level
folder. From a linked context, destructive operations in ANY OTHER project are blocked until the
operator says otherwise. An UNLINKED context (general chat, DMs, CLI) is not contained — it may
work across projects, but the root/scaffolding protection still holds everywhere.

Only destructive operations are gated (rm/rmdir/shred/dd/mkfs/truncate, ``find -delete``,
``xargs rm``, overwrite redirects); ``mv`` only warns. Reading is never blocked here — staying
out of other projects while linked is a behavioural rule, not a sandbox.

Two entry points share one checker:
  * ``check_command(cmd, cwd, home)`` / ``guard_tool_call(name, args, ...)`` — in-process,
    wired into ``core.agent_loop.run_agent(guard_fn=...)``.
  * ``scripts/workspace_guard_hook.py`` — a Claude-Code-style PreToolUse hook (stdin JSON,
    exit 2 = block) for the ``claude_code`` provider.

Zero LLM calls; pure path arithmetic.  # NEXUS:PORTABLE
"""
from __future__ import annotations

import glob
import os
import re
import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

DEFAULT_SCAFFOLD = frozenset({
    "AGENTS.md", "CLAUDE.md", "GEMINI.md", "README.md", "src", "docs",
    ".workspace-tools.yml", ".claude", ".git", ".env",
})

DESTROY = re.compile(r'\b(rm|rmdir|shred|dd|mkfs\S*|truncate)\b')   # data loss → block-eligible
MOVE = re.compile(r'\bmv\b')                                        # rename/move → warn only
_OPS = set(';&|')
_VERBS = {'rm', 'rmdir', 'shred', 'truncate', 'dd', 'mv', 'sudo', 'xargs'}


@dataclass
class Verdict:
    allowed: bool
    reason: str = ""
    warnings: list[str] = field(default_factory=list)


def _layout():
    """layout.py loaded by file path — importing the `src.core` package pulls in the whole
    engine (seconds), and the hook entrypoint runs on every shell call."""
    import importlib.util
    name = "_nexus_layout"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name("layout.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


def workspace_root() -> Path:
    """The install's workspace root, resolved from the layout manifest (never hardcoded)."""
    return _layout().path("workspace")


class WorkspaceGuard:
    def __init__(self, root: Optional[os.PathLike | str] = None,
                 scaffold: Iterable[str] = DEFAULT_SCAFFOLD):
        self.root = os.path.normpath(str(root if root is not None else workspace_root()))
        self.scaffold = frozenset(scaffold)

    # ── ownership ────────────────────────────────────────────────────────────
    def office(self, path: str | None) -> Optional[str]:
        """Top-level project dir name under the workspace root, or None if outside it."""
        if not path:
            return None
        p = os.path.normpath(str(path))
        if not p.startswith(self.root + os.sep):
            return None
        return p[len(self.root) + 1:].split(os.sep)[0] or None

    def _manifest_protected(self, pdir: str) -> set[str]:
        mf = os.path.join(pdir, ".workspace-tools.yml")
        try:
            txt = open(mf).read()
        except OSError:
            return set()
        block = re.search(r'^protected_paths:\n((?:- .*\n)*)', txt, re.M)
        lines = block.group(1).splitlines() if block else []
        return {l[2:].strip().strip('"\'').rstrip('/') for l in lines}

    def is_protected(self, path: str) -> bool:
        """True for the office root, a scaffold entry itself, or a parent of one."""
        name = self.office(path)
        if name is None:
            return False
        pdir = os.path.join(self.root, name)
        rel = os.path.relpath(os.path.normpath(path), pdir).rstrip('/')
        if rel == '.':
            return True
        entries = self.scaffold | self._manifest_protected(pdir)
        return any(rel == e or e.startswith(rel + '/') for e in entries)

    # ── command parsing ──────────────────────────────────────────────────────
    @staticmethod
    def _segments(cmd: str):
        """Quote-aware split into command segments: `grep -vE 'a|b'` is not a pipe."""
        try:
            lx = shlex.shlex(cmd.replace('\n', ' ; '), posix=True, punctuation_chars=True)
            lx.whitespace_split = True
            seg: list[str] = []
            for t in lx:
                if t and set(t) <= _OPS:
                    yield seg
                    seg = []
                else:
                    seg.append(t)
            yield seg
        except ValueError:                              # unbalanced quotes → crude split
            for part in re.split(r'&&|\|\||;|\n|\|', cmd):
                yield [t.strip('"\'') for t in part.split()]

    def _resolve(self, t: str, cwd: str) -> list[str]:
        t = os.path.expanduser(t)
        t = os.path.normpath(t if os.path.isabs(t) else os.path.join(cwd, t))
        return (glob.glob(t) or [t]) if any(c in t for c in '*?[') else [t]   # rm * → every match

    def _path_tokens(self, seg: list[str], cwd: str) -> list[str]:
        out: list[str] = []
        in_ws = self.office(cwd) is not None or cwd == self.root
        for t in seg:
            if t.startswith(('of=', 'if=')):              # dd of=/path
                t = t[3:]
            if not t or t.startswith('-') or t in _VERBS:
                continue
            if t.startswith(('/', '~', '.')) or '/' in t or in_ws:
                out += self._resolve(t, cwd)
        return out

    def extract_targets(self, cmd: str, cwd: str) -> tuple[list[str], bool, bool]:
        """(operand paths of destructive/move segments, destroy?, move?). A project root merely
        named elsewhere (`cd <proj> && rm x`) is NOT a target; `cd` is tracked."""
        paths: list[str] = []
        prev: list[str] = []
        destroy = move = False
        for seg in self._segments(cmd):
            if not seg:
                continue
            if seg[0] == 'cd' and len(seg) > 1:
                d = os.path.expanduser(seg[1])
                cwd = os.path.normpath(d if os.path.isabs(d) else os.path.join(cwd, d))
                prev = []
                continue
            line = ' '.join(seg)
            toks = self._path_tokens(seg, cwd)
            d, m = bool(DESTROY.search(line)) or '-delete' in seg, bool(MOVE.search(line))
            if d or m:
                destroy |= d
                move |= m
                paths += toks
                if seg[0] == 'xargs':                   # find ... | xargs rm
                    paths += prev
            prev = toks
        for m in re.finditer(r'>\s*([^\s;&|]+)', cmd):    # overwrite redirect
            destroy = True
            paths += self._resolve(m.group(1), cwd)
        return paths, destroy, move

    # ── verdicts ─────────────────────────────────────────────────────────────
    def check_paths(self, paths: Iterable[str], *, destroy: bool,
                    home: Optional[str] = None) -> Verdict:
        """`home` = the linked context's project dir (any depth) or None when unlinked."""
        home_office = self.office(home) if home else None
        blocks, warns = [], []
        for ap in paths:
            name = self.office(ap)
            if name is None and os.path.normpath(ap) != self.root:
                continue
            if name is None:                            # the workspace root itself
                if destroy:
                    blocks.append(f"{ap} → workspace root")
                continue
            protected = self.is_protected(ap)
            foreign = home_office is not None and name != home_office
            tag = (f"{ap} → project '{name}'" + (" PROTECTED" if protected else "")
                   + (f" (outside this context's project '{home_office}')" if foreign else ""))
            (blocks if destroy and (protected or foreign) else warns).append(tag)
        if blocks:
            return Verdict(False, (
                "⛔ workspace guard — destructive op on protected paths:\n  " + "\n  ".join(blocks)
                + "\n\nProject roots, scaffolding/operational files, and other projects (from a "
                  "linked context) need the operator's confirmation. Everything else in this "
                  "context's project is yours — narrow the command to exclude these."), warns)
        return Verdict(True, "", warns)

    def check_command(self, cmd: str, cwd: Optional[str] = None,
                      home: Optional[str] = None) -> Verdict:
        cwd = os.path.normpath(cwd or os.getcwd())
        paths, destroy, move = self.extract_targets(cmd, cwd)
        if not (destroy or move):
            return Verdict(True)
        return self.check_paths(paths, destroy=destroy, home=home)


_DELETE_TOOL = re.compile(r'(delete|remove|unlink|rmdir|rm)(_|$)', re.I)


def guard_tool_call(name: str, args: dict, *, cwd: Optional[str] = None,
                    home: Optional[str] = None,
                    guard: Optional[WorkspaceGuard] = None) -> Verdict:
    """Map a neutral tool call onto the checker: shell-style tools carry `command`;
    delete-style tools carry `path`/`file_path`. Anything else passes."""
    g = guard or WorkspaceGuard()
    args = args or {}
    cwd = cwd or home or os.getcwd()
    if isinstance(args.get("command"), str):
        return g.check_command(args["command"], cwd=cwd, home=home)
    target = args.get("path") or args.get("file_path")
    if isinstance(target, str) and _DELETE_TOOL.search(name or ""):
        t = os.path.normpath(target if os.path.isabs(target) else os.path.join(cwd, target))
        return g.check_paths([t], destroy=True, home=home)
    return Verdict(True)
