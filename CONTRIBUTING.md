# Contributing to Multi-LLM-Nexus

Thanks for your interest. Nexus is an LLM-agnostic, self-hosted agent platform, and
contributions that keep it provider-neutral and operator-controlled are very welcome.

This is an actively developed project in **Active Beta** — expect rough edges, and please
open an issue to discuss anything substantial before sending a large change.

## Ground rules

- **Provider neutrality is the point.** Commands and workflows bind to capability tiers
  (`nano` / `standard` / `deep`), never to hardcoded model names. Don't introduce code that
  assumes one vendor.
- **Operator control.** Behavior should be configurable, not baked in. Prefer config
  (`config/*.yaml`, `.env`) over hardcoded paths, names, or credentials.
- **Keep secrets out.** Never commit API keys, tokens, or real `.env` files. Use
  `.env.example` for documentation.

## Getting set up

```bash
git clone https://github.com/Driftah9/multi-llm-nexus
cd multi-llm-nexus
./setup.sh          # interactive wizard: pick providers + adapters, writes config

# or a manual dev install
python -m venv .venv && source .venv/bin/activate
pip install -e ".[all]"
```

Requires **Python 3.11+**.

## Running tests

The suite uses `pytest` (config in `pyproject.toml`, `asyncio_mode = auto`):

```bash
pip install pytest pytest-asyncio
pytest                      # full suite
pytest tests/test_provider_chain.py   # a single module
```

Please add or update tests under `tests/` for any behavior change to core
(`src/core/*`), providers (`src/providers/*`), or adapters (`src/adapters/*`).

## Submitting changes

1. Fork the repo and create a topic branch (`feat/...`, `fix/...`, `docs/...`).
2. Make focused commits. We loosely follow
   [Conventional Commits](https://www.conventionalcommits.org/) — e.g.
   `feat(core): add pool-aware routing`, `fix(providers): handle cohere rate limit`.
3. Run `pytest` and confirm it passes.
4. Open a pull request describing **what** changed and **why**. Link any related issue.

## Adding a provider

New providers live in `src/providers/` and implement the shared provider interface
(see `src/providers/base.py` and an existing module like `openai.py` for the pattern).
Register the provider and its cost class in `src/providers/registry.py`. A provider that
speaks the OpenAI-compatible API can often be reached through the existing OpenAI adapter
with a different base URL rather than a new module — prefer that where it fits.

## Reporting bugs and security issues

- **Bugs / features:** open a GitHub issue with steps to reproduce, your provider/adapter
  config (redact secrets), and what you expected.
- **Security vulnerabilities:** do **not** open a public issue — see
  [SECURITY.md](SECURITY.md).

## License

By contributing, you agree that your contributions are licensed under the
[MIT License](LICENSE) that covers the project.
