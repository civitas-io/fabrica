# Contributing to Fabrica

Thank you for contributing.

## Dev setup

Fabrica uses [`uv`](https://docs.astral.sh/uv/) for dependency management.

```bash
# Clone and enter the repo
git clone https://github.com/civitas-io/fabrica
cd fabrica

# Install all dev dependencies (includes test, lint, type-check tooling)
uv sync --extra dev

# Install pre-commit hooks -- ruff/ruff-format/gitleaks run on every commit;
# mypy --strict and the real test suite run on every push (kept separate
# from commit since the full suite is slower, especially the hardware-gated
# Firecracker/srt tests). See .pre-commit-config.yaml for exactly what runs.
uv run pre-commit install
uv run pre-commit install --hook-type pre-push
```

That's it. No virtualenv activation needed — `uv run` handles the environment.

## Running checks manually

These match exactly what `.github/workflows/test-build-release.yml` runs in CI:

```bash
uv run ruff check src/ tests/
uv run mypy --strict src/
uv run pytest tests/ --ignore=tests/tunnel \
    --deselect tests/sandbox/test_srt_backend.py::test_execute_allows_network_to_allowlisted_domain \
    --deselect "tests/mcp/test_server_stress.py::TestConcurrentSandboxContention::test_n_agents_exceed_max_concurrent_and_all_still_succeed_correctly"
```

Real, hardware-gated tests (Firecracker microVM, `srt` sandboxing) automatically skip on a host
without the required hardware/binary — you do not need to skip them manually. They're verified
for real on the homelab; see `specs/archive/spikes/` for the research trail.

`specs/archive/spikes/scripts/` (real, kept-not-deleted historical spike code) is deliberately
**not** covered by `ruff check`/`ruff format` in CI or in the pre-commit hooks here — it's an
archival record of real findings, not actively maintained production code.

## Standards

- **Python:** ≥3.12
- **Linting:** Ruff, 100 char line length
- **Type checking:** mypy `--strict`
- **Testing:** pytest + pytest-asyncio + pytest-cov
- **Build:** hatchling via uv

## Where to start

- [HANDOFF.md](HANDOFF.md) — current, real, dated status; read this first, it's kept in sync
  with what's actually shipped, not what was originally planned.
- [docs/PLAN.md](docs/PLAN.md) — the single ordered work queue.
- `docs/contracts/*.md` — implementation-ready component contracts, if you're adding a new
  capability rather than fixing something in an existing one.

## PR conventions

- One logical change per PR.
- Tests are required for new functionality; bug fixes should include a regression test.
- Real, not mocked, verification for anything with a genuine hardware/network/external-service
  dependency — this project's own established discipline (see `specs/archive/spikes/` for why).
- Commit messages: short imperative first line, bullet body if needed.

## Code of Conduct

Be respectful. Be constructive. Focus on the work.
