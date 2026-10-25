# Elyntro repository inclusion manifest

This document captures the proposed canonical source set for controlled Git inclusion. It is an inventory, not a claim that every integration test currently passes or authorization to stage the files.

## Canonical source to include

The following files and directories make up the current application source, its local engine dependency, tests, deployment configuration, and supporting documentation. The Flask application imports the local `elyntro` package and `search_providers.py`; `tool_runtime.py` is also a separately tested project module.

- `.gitignore`
- `README.md`
- `app.py`
- `search_providers.py`
- `tool_runtime.py`
- `render.yaml`
- `requirements.txt`
- `requirements-dev.txt`
- `requirements.lock`
- `requirements-dev.lock`
- `static/index.html`
- `docs/`
- `tests/`
- `elyntro-engine-v6.2.0/`

The engine package directory supplies the `elyntro` imports used by `app.py`; its `pyproject.toml` declares Python `>=3.10`, consistent with the application lock files resolved on Python 3.14. `search_providers.py` is present and its provider/fetch regressions are included in `tests/test_search_providers.py`.

## Explicit exclusions

Do not add these local or generated files to the proposed integration set:

- Credentials, private keys, and secret-bearing files, including `.env`, `.env.*`, `*.pem`, `*.key`, `*.p12`, and `*.pfx`.
- Local SQLite/runtime state, including `*.db`, `*.sqlite*`, and `*.log`.
- Python bytecode and caches, including `__pycache__/`, `*.py[cod]`, `.pytest_cache/`, `.mypy_cache/`, and `.ruff_cache/`.
- Virtual environments, including `.venv/`, `venv/`, `.tox/`, `.nox/`, and `node_modules/`.
- Test/coverage output and temporary files, including `.coverage`, `coverage/`, `htmlcov/`, `*.tmp`, editor swap/backup files, temporary test databases, and `/tmp` audit artifacts.
- Build output and package metadata, including `dist/`, `build/`, and `*.egg-info/`.
- Generated working ZIP bundles and other archives not already tracked by Git.

The 12 historical ZIP archives in the repository root are already tracked by Git. They are not part of the new source inclusion set. Do not remove, rewrite, or add ZIP changes as part of this integration; changing the already-tracked archives requires a separate explicit decision. The root `.gitignore` excludes the local database, environment files, caches, bytecode, logs, build outputs, and ZIP files from new additions.

## Current Git and verification status

The application, engine, tests, documentation, dependency files, and Render configuration are present in the working tree. The files listed above are not necessarily tracked or staged: consult `git status --short --untracked-files=all` before integration. Run the full suite and `pip check` from the selected locked environment before deciding whether the set is ready to integrate. Tests use mocked providers; do not make real Tavily or Groq requests as part of routine local verification.

The Render blueprint currently selects the free plan and declares no persistent disk. SQLite therefore uses ephemeral service storage unless `ELYNTRO_DB_PATH` is configured to a separately authorized persistent mount. A service replacement or redeploy can discard database contents. Do not add a disk or change the plan without approval.

## Safe staging command (manual, not executed)

This is a candidate command for a later, explicit staging approval; it was not run. It names the source, provider, runtime, tests, dependency manifests/locks, deployment configuration, UI, documentation, and engine. The repository ignore rules exclude local runtime state and generated files.

```bash
git add .gitignore README.md app.py search_providers.py tool_runtime.py render.yaml \
  requirements.txt requirements-dev.txt requirements.lock requirements-dev.lock \
  static/index.html docs tests elyntro-engine-v6.2.0
```

Do not run this command until the user explicitly approves staging. It does not include root ZIP archives; do not stage or alter those archives as part of this integration. No commit, push, or deployment is authorized by this manifest.
