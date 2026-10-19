#!/usr/bin/env python3
"""secrets_lint — find plaintext secrets in what this repo would publish.

Ported from live claude-brain (2026-10-19), retargeted at Nexus design rule #4 ("public-safe —
no real credentials in any file"). Scans every file git TRACKS (exactly what a push publishes);
`--all` adds untracked, non-ignored files. Prints file:line:kind only — never the value.
Exit 1 if anything is found, so a pre-push hook or run_tracked/nightly_sweeper surfaces it.

Allowlist: config/secrets-lint-allow.txt — one path prefix per line, '#' comments.
`*.example`, `*.template`, `*.sample` files and symlinks are skipped by design.

    python3 scripts/secrets_lint.py          # tracked files
    python3 scripts/secrets_lint.py --all    # + untracked, non-ignored

# NEXUS:PORTABLE — zero LLM calls.
"""
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ALLOW_FILE = REPO / "config" / "secrets-lint-allow.txt"
SUFFIXES = (".py", ".sh", ".json", ".yml", ".yaml", ".env", ".toml", ".js", ".ts", ".cfg", ".ini",
            ".conf", ".service", ".md", ".txt")
MAX_BYTES = 300_000

KNOWN = {
    "telegram-bot": r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b",
    "discord-bot": r"\b[MN][A-Za-z\d]{23,25}\.[\w-]{6}\.[\w-]{27,}\b",
    "openai-style": r"\bsk-[A-Za-z0-9_-]{20,}\b",
    "github": r"\bgh[pousr]_[A-Za-z0-9]{30,}\b",
    "slack": r"\bxox[abpr]-[A-Za-z0-9-]{10,}\b",
    "google-api": r"\bAIza[0-9A-Za-z_-]{35}\b",
    "nvidia": r"\bnvapi-[A-Za-z0-9_-]{30,}\b",
    "groq": r"\bgsk_[A-Za-z0-9]{30,}\b",
    "aws-access-key": r"\bAKIA[0-9A-Z]{16}\b",
    "private-key": r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
}
# Markdown: a secret word, then a long backticked literal on the same line.
MD_LITERAL = re.compile(r"(?i)(token|secret|api[_ -]?key|apikey|passw|bearer)[^`\n]{0,60}`([A-Za-z0-9_\-\.\+/=:]{24,})`")
ASSIGN = re.compile(
    r"""(?ix)["']?([A-Za-z0-9_\-]*(?:token|secret|api[_-]?key|apikey|passw(?:or)?d|bearer)[A-Za-z0-9_\-]*)["']?
        \s*[:=]\s*["']([A-Za-z0-9_\-\.\+/=:]{20,})["']""")
SAFE_VALUE = re.compile(r"(?i)(example|your[_-]|xxx|placeholder|changeme|resolved at runtime|<|\$\{|\.\.\.|^[a-z_]+$|dummy|test[_-]?token|fake)")


def allowed():
    out = []
    if ALLOW_FILE.exists():
        for l in ALLOW_FILE.read_text().splitlines():
            l = l.split("#")[0].strip()
            if l:
                out.append(l)
    return out


def files(include_untracked: bool = False, root: Path = REPO):
    args = ["git", "ls-files", "-z"] + (["--others", "--exclude-standard", "--cached"] if include_untracked else [])
    res = subprocess.run(args, cwd=root, capture_output=True, text=True, check=True)
    return [f for f in res.stdout.split("\0") if f]


def scan(rels, root: Path = REPO, allow=()):
    findings = []
    for rel in rels:
        if any(rel.startswith(a) for a in allow):
            continue
        p = root / rel
        if (p.is_symlink() or not p.is_file() or ".example" in rel
                or rel.endswith((".template", ".sample")) or not rel.endswith(SUFFIXES)
                and not Path(rel).name.startswith(".env")):
            continue
        try:
            if p.stat().st_size > MAX_BYTES:
                continue
            lines = p.read_text(errors="ignore").splitlines()
        except OSError:
            continue
        for n, line in enumerate(lines, 1):
            if len(line) > 600:
                continue
            for kind, rx in KNOWN.items():
                if re.search(rx, line):
                    findings.append((rel, n, kind))
            if rel.endswith(".md"):
                m = MD_LITERAL.search(line)
                if (m and not SAFE_VALUE.search(m.group(2)) and re.search(r"\d", m.group(2))
                        and not re.search(r"[/\\]", m.group(2))):
                    findings.append((rel, n, f"md-literal:{m.group(1)[:30]}"))
                continue
            m = ASSIGN.search(line)
            if m and not SAFE_VALUE.search(m.group(2)) and not re.search(r"(?i)os\.environ|getenv|get\(", line):
                findings.append((rel, n, f"literal:{m.group(1)[:30]}"))
    return list(dict.fromkeys(findings))


def main(argv=None, root: Path = REPO):
    argv = sys.argv[1:] if argv is None else argv
    found = scan(files("--all" in argv, root=root), root=root,
                 allow=allowed() if root == REPO else ())
    for f in found:
        print(f"{f[0]}:{f[1]}:{f[2]}")
    print(f"secrets_lint: {len(found)} finding(s) in {len({f[0] for f in found})} file(s)", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
