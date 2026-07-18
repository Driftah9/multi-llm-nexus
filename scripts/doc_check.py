#!/usr/bin/env python3
"""doc_check — surface which docs make claims about the code you just changed.

STEP 1 before any documentation edit, and part of every live→Nexus port. Docs drift
because code changes ship without the docs that describe them. This tool closes that gap:
give it the changed files (or a git diff) and it tells you exactly which docs to reconcile
BEFORE you touch them — so nothing silently goes stale.

It finds affected docs two ways:
  1. Direct mention — greps every .md for the changed file's module/basename.
  2. Claim map — matches changed paths against docs/DOC_INDEX.yml, which records the
     *conceptual* claims a doc makes about a subsystem (claims that don't name the file).

Read-only. No network. Exits 0 always (advisory tool), except on bad usage.

Usage:
  python scripts/doc_check.py src/core/pool_manager.py [more paths...]
  python scripts/doc_check.py --diff [BASE]     # git diff --name-only BASE (default: HEAD)
  python scripts/doc_check.py --staged          # git diff --cached --name-only
  python scripts/doc_check.py --diff origin/main # what a PR against main would change

# NEXUS:PORTABLE — the mechanism is generic; DOC_INDEX.yml is the only install-specific part.
"""
from __future__ import annotations

import subprocess
import sys
from fnmatch import fnmatch
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = ROOT / "docs" / "DOC_INDEX.yml"

# Directories we never scan for docs / never treat as "your change needs docs".
_SKIP_DIRS = {".git", "node_modules", ".pytest_cache", ".venv", "venv", "__pycache__"}
# A changed file in one of these is itself documentation — no reconciliation needed.
_DOC_SUFFIXES = {".md"}


def _run_git(args: list[str]) -> list[str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), *args],
            capture_output=True, text=True, check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"doc_check: git failed ({e}); pass file paths explicitly instead.", file=sys.stderr)
        return []
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def changed_from_args(argv: list[str]) -> list[str]:
    if not argv:
        print(__doc__.split("Usage:")[1].rstrip(), file=sys.stderr)
        sys.exit(2)
    if argv[0] == "--staged":
        return _run_git(["diff", "--cached", "--name-only"])
    if argv[0] == "--diff":
        base = argv[1] if len(argv) > 1 else "HEAD"
        return _run_git(["diff", "--name-only", base])
    return argv


def all_docs() -> list[Path]:
    docs = []
    for p in ROOT.rglob("*.md"):
        if any(part in _SKIP_DIRS for part in p.relative_to(ROOT).parts):
            continue
        docs.append(p)
    return docs


def _load_index() -> list[dict]:
    """Parse docs/DOC_INDEX.yml. pyyaml if available; else a tiny tolerant fallback so the
    tool still works on a bare Python. Returns a list of {area, paths[], docs[], claim}."""
    if not INDEX_PATH.exists():
        return []
    text = INDEX_PATH.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(text) or {}
        return data.get("subsystems", [])
    except Exception:
        return _mini_parse_index(text)


def _mini_parse_index(text: str) -> list[dict]:
    """Minimal parser for the specific DOC_INDEX.yml shape (no pyyaml dependency)."""
    subs: list[dict] = []
    cur: dict | None = None
    key = None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw.lstrip().startswith("- area:"):
            cur = {"area": raw.split("area:", 1)[1].strip().strip('"'), "paths": [], "docs": [], "claim": ""}
            subs.append(cur)
            key = None
        elif cur is not None and raw.strip().endswith(":") and raw.strip()[:-1] in ("paths", "docs"):
            key = raw.strip()[:-1]
        elif cur is not None and raw.lstrip().startswith("claim:"):
            cur["claim"] = raw.split("claim:", 1)[1].strip().strip('"')
            key = None
        elif cur is not None and key and raw.lstrip().startswith("- "):
            cur[key].append(raw.lstrip()[2:].strip().strip('"'))
    return subs


def stem_tokens(path: str) -> list[str]:
    """Search tokens for a changed file: the module stem and the repo-relative path."""
    p = Path(path)
    toks = {p.name, p.stem, path}
    # module dotted form, e.g. src/core/pool_manager.py -> core.pool_manager
    if p.suffix == ".py":
        parts = list(p.with_suffix("").parts)
        if parts and parts[0] == "src":
            parts = parts[1:]
        toks.add(".".join(parts))
    return [t for t in toks if len(t) >= 4]


def main() -> int:
    changed = changed_from_args(sys.argv[1:])
    code_changed = [c for c in changed if Path(c).suffix not in _DOC_SUFFIXES]
    docs_changed = [c for c in changed if Path(c).suffix in _DOC_SUFFIXES]

    if not code_changed:
        print("doc_check: no code/config changes in the set — nothing to reconcile.")
        if docs_changed:
            print(f"  ({len(docs_changed)} doc file(s) changed; remember the 'Last verified' stamp.)")
        return 0

    docs = all_docs()
    index = _load_index()
    review: dict[str, set[str]] = {}   # doc -> {reasons}

    def note(doc: str, reason: str):
        review.setdefault(doc, set()).add(reason)

    # 1) direct mentions
    doc_text = {d: d.read_text(encoding="utf-8", errors="ignore") for d in docs}
    for cf in code_changed:
        for tok in stem_tokens(cf):
            for d, txt in doc_text.items():
                if tok in txt:
                    note(str(d.relative_to(ROOT)), f"mentions `{tok}` (from {cf})")

    # 2) claim-map matches
    index_hits: list[tuple[str, str, list[str]]] = []  # (area, claim, docs)
    for sub in index:
        globs = sub.get("paths", [])
        matched = [cf for cf in code_changed if any(fnmatch(cf, g) for g in globs)]
        if matched:
            index_hits.append((sub.get("area", "?"), sub.get("claim", ""), sub.get("docs", [])))
            for d in sub.get("docs", []):
                note(d, f"claim-map: {sub.get('area','?')}")

    # ---- report ----
    print("=" * 72)
    print("DOC RECONCILIATION — review these docs BEFORE editing, ship fixes WITH the code")
    print("=" * 72)
    print(f"\nChanged code/config ({len(code_changed)}):")
    for cf in code_changed:
        print(f"  • {cf}")

    if index_hits:
        print("\nSubsystem claims touched (from DOC_INDEX.yml):")
        for area, claim, ds in index_hits:
            print(f"  ▸ {area}")
            if claim:
                print(f"      current claim to keep true: {claim}")
            for d in ds:
                print(f"      → {d}")

    print(f"\nAll docs to review ({len(review)}):")
    if not review:
        print("  (none matched — but confirm by hand; the map may be missing this subsystem,")
        print("   in which case add an entry to docs/DOC_INDEX.yml.)")
    for doc in sorted(review):
        reasons = "; ".join(sorted(review[doc]))
        print(f"  ☐ {doc}\n      {reasons}")

    # drift guard: code changed but no doc changed in the same set
    if not docs_changed:
        print("\n⚠  No documentation files are in this change set.")
        print("   If any claim above is now wrong, fix the doc IN THIS SAME COMMIT/PR.")
        print("   If nothing changed for readers, that's fine — this is advisory.")

    print("\nReminder: update each touched doc's \"Last verified against code: <date>\" stamp.")
    print("Protocol: docs/DOC_SYNC_PROTOCOL.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
