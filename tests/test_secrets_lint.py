"""secrets_lint (ported from live 2026-10-19) — finds key-shaped literals, never prints values."""
import importlib.util
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("_secrets_lint", REPO / "scripts" / "secrets_lint.py")
sl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sl)

FAKE_GH = "gh" + "p_" + "A1b2C3d4" * 5                  # assembled so this file never matches itself
FAKE_SK = "s" + "k-" + "Zq9" * 9
FAKE_LIT = "Q7wE9rT1" * 3                                # a 24-char non-placeholder value


def _write(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return rel


def test_known_patterns_and_literals(tmp_path):
    rels = [
        _write(tmp_path, "a.py", f'TOKEN = "{FAKE_GH}"\n'),
        _write(tmp_path, "b.yaml", "api" + '_key: "' + FAKE_LIT + '"\n'),
        _write(tmp_path, "c.md", f"Use the bearer token `{FAKE_SK}` for calls\n"),
    ]
    found = {(f[0], f[2].split(":")[0]) for f in sl.scan(rels, root=tmp_path)}
    assert ("a.py", "github") in found and ("b.yaml", "literal") in found
    assert ("c.md", "openai-style") in found


def test_safe_values_examples_and_env_lookups_pass(tmp_path):
    rels = [
        _write(tmp_path, "ok.py", 'api_key = os.environ.get("OPENAI_API_KEY")\n'
                                 'token = "your_token_here_placeholder_x"\n'),
        _write(tmp_path, "cfg.yaml", "bot_token: ${MATTERMOST_BOT_TOKEN}\n"),
        _write(tmp_path, "keys.yaml.example", f'token: "{FAKE_GH}"\n'),
    ]
    assert sl.scan(rels, root=tmp_path) == []


def test_allowlist_prefix(tmp_path):
    rel = _write(tmp_path, "tests/fixture.py", f'K = "{FAKE_SK}"\n')
    assert sl.scan([rel], root=tmp_path) and not sl.scan([rel], root=tmp_path, allow=["tests/"])


def test_output_never_contains_the_value(tmp_path, capsys):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    _write(tmp_path, "leak.py", f'X = "{FAKE_GH}"\n')
    subprocess.run(["git", "add", "leak.py"], cwd=tmp_path, check=True)
    assert sl.main([], root=tmp_path) == 1
    out = capsys.readouterr()
    assert "leak.py:1:github" in out.out and FAKE_GH not in out.out + out.err


def test_repo_is_clean():
    """Design rule #4 as a test: nothing key-shaped in tracked files (allowlist applies)."""
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "secrets_lint.py")],
                       capture_output=True, text=True, cwd=REPO)
    assert r.returncode == 0, r.stdout
