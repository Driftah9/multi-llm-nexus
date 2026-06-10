# Security Policy

## Supported versions

Multi-LLM-Nexus is in **Active Beta**. Security fixes are applied to the latest released
version on the `main` branch. There is no long-term-support branch yet; please run a
recent release.

| Version | Supported |
| ------- | --------- |
| Latest `main` / newest tagged release | ✅ |
| Older tags | ❌ (upgrade to latest) |

## Reporting a vulnerability

**Please do not report security vulnerabilities through public GitHub issues, pull
requests, or discussions.**

Report privately through GitHub's built-in private vulnerability reporting:

1. Go to the repository's **Security** tab → **Report a vulnerability**
   (Security Advisories), or visit
   <https://github.com/Driftah9/multi-llm-nexus/security/advisories/new>.
2. Describe the issue, the affected component (provider, adapter, core, wizard, or
   mesh), steps to reproduce, and the potential impact.

You can expect an acknowledgement within a few days. Because this is a solo-maintained
beta project, please allow reasonable time for a fix before any public disclosure —
coordinated disclosure is appreciated.

## Scope and handling notes

Nexus is self-hosted and operator-controlled. A few things that are **operator
responsibility**, not platform vulnerabilities, but are still worth flagging if the
defaults are unsafe:

- **Secrets:** API keys and tokens live in your `.env` / `config/`. Never commit them.
  Reports about secrets accidentally committed to *this* repo are always in scope.
- **Adapter exposure:** chat-platform connectors (Mattermost, Discord, Telegram) and any
  network-listening component should bind only where you intend. Report unsafe defaults.
- **Local LLM / mesh transport:** issues in the provider abstraction, the OpenAI-compatible
  API adapter, or mesh networking that could leak data across operators are in scope.

Thank you for helping keep Nexus and its operators safe.
