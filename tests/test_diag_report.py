"""Diagnostic report — structure, fail-closed redaction, and no-config safety."""
from src.core import diag_report


def test_generates_expected_sections():
    md = diag_report.generate_markdown()
    for heading in ("# Nexus Diagnostic Report", "## Build", "## System",
                    "## Feature gates", "## Providers", "## Provider health",
                    "## Local services", "## Memory backends"):
        assert heading in md


def test_states_no_phone_home():
    md = diag_report.generate_markdown()
    assert "No phone-home" in md or "no telemetry" in md
    assert "never contains" in md  # the what-it-omits disclosure


def test_redactor_masks_secrets_always():
    dirty = (
        "OPENAI_API_KEY=sk-abcdef0123456789abcdef\n"
        "Authorization: Bearer abcdef0123456789abcdef\n"
        "contact me at someone@example.com\n"
        "password = hunter2hunter2\n"
    )
    out = diag_report.scrub(dirty, redact_paths=False)
    assert "sk-abcdef0123456789abcdef" not in out
    assert "hunter2hunter2" not in out
    assert "someone@example.com" not in out
    assert "‹redacted" in out


def test_redactor_masks_paths_and_lan_ips_by_default():
    dirty = "config at /home/operator/.env on host 10.0.0.7 and 192.168.1.5"
    out = diag_report.scrub(dirty, redact_paths=True)
    assert "/home/operator" not in out
    assert "10.0.0.7" not in out and "192.168.1.5" not in out
    assert "‹lan-ip›" in out and "~" in out


def test_redact_paths_can_be_disabled_but_secrets_still_masked():
    dirty = "/home/operator key=sk-deadbeefdeadbeef00"
    out = diag_report.scrub(dirty, redact_paths=False)
    assert "/home/operator" in out          # paths preserved when disabled
    assert "sk-deadbeefdeadbeef00" not in out  # secrets masked regardless


def test_report_filename_is_timestamped_md():
    name = diag_report.report_filename()
    assert name.startswith("nexus-diag-") and name.endswith(".md")
