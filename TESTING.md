# Testing the production workspace

```powershell
uv sync --frozen
uv run --no-sync python -m pytest -q test/services/test_production.py
uv run --no-sync ruff check app/production app/launcher.py test/services/test_production.py
node --check app/production/web/workspace.js
```

The production tests use temporary directories, mock paid calls, test quote idempotency and concurrent caps, and exercise cancellation, dependency changes, project ownership, upload normalization, and reconciliation after restart. The render test creates actual H.264 footage, uses deterministic PCM fixtures for both narration languages, builds a watermarked draft and a final MP4 with sound effects and background audio, verifies embedded language tags with PyAV, validates separate SRT captions, and decodes an exported frame.

The launch and browser smoke checks exercise the local script and project entry points. Live provider tests require suitable account access and explicit cost authorization. This suite uses no paid credits.

The Windows end-to-end smoke run used `mpt production`, an isolated temporary workspace, both installed system voices, and the running API from research review through final export. It decoded a 1080p frame, inspected both AAC streams and both subtitle streams, downloaded both SRT files, and confirmed the project reached 100% progress. A wheel build included all three browser assets.

To repeat the Python advisory scan, export the lock with `uv export --frozen --no-dev --no-hashes --no-emit-project --format requirements-txt --output-file audit-requirements.txt` and scan it with `uvx --from pip-audit==2.10.0 pip-audit -r audit-requirements.txt --no-deps --disable-pip`.
