# MoneyPrinterTurbo architecture audit

Baseline `bdd8d32`, inspected 2026-11-19. Scope covers the local checkout and its default entry points.

| Area | Existing implementation and reuse decision |
| --- | --- |
| Script and AI models | `app/services/llm.py:927` generates scripts through a broad provider catalog. The legacy API in `app/controllers/v1/llm.py` can dispatch directly. Its transport is unsuitable for the new paid-job boundary without controlling retries and credential scope. |
| Images and video | `app/services/material.py` orchestrates stock, image and video sources. Existing video adapters include Seedance, UseAPI/Kling, MuAPI and other gateways. Their capabilities and billing behavior vary. |
| Narration, captions and audio | `app/services/voice.py:1159` exposes multiple speech backends. Subtitle alignment and writer code live in `app/services/subtitle.py` and `app/utils/subtitle_writer.py`. `app/services/bgm.py` and `app/services/video.py` handle music and composition. |
| Prepared-media projects | `app/services/video_project.py:292` snapshots local media and `:540` renders cached revisions. It accepts prepared files and stores revisions on disk. It has no persisted review, spending or ownership model. Reuse the prepared-media renderer and subtitle writer where the runtime supports them. |
| Jobs | `app/services/state.py:69` has in-process state and `:134` has Redis state. `app/asgi.py:46` resumes Redis queues. Existing tasks lack a transaction tying project revision, operator approval and budget reservation to provider submission. |
| API and UI | `app/asgi.py` mounts v1 routes and task artifacts. `webui/Main.py` is the Streamlit interface and `app/launcher.py` defaults it to loopback. Legacy runs serve existing callers and require separate operational controls. |
| Configuration | `app/config/config.py:532` loads TOML and `:596` persists settings. Multiple legacy providers support environment variables. `pyproject.toml`, `requirements-security.txt` and `uv.lock` carry pins and the Pillow override. |

## Verified findings

| ID | Severity | Evidence and impact |
| --- | --- | --- |
| A01 | High when reachable | `main.py:12` uses `app/config/config.py:695`, whose fallback is `0.0.0.0`. `config.example.toml:52` defaults the shared API key to empty, and `app/controllers/base.py:58` allows legacy calls in that configuration. A reachable listener can expose task operations, artifacts and paid generation if provider credentials are configured. Default Compose host ports are loopback-bound. |
| A02 | Medium | Legacy keys can be persisted in plaintext TOML by `app/config/config.py:625`. Settings access and configuration backups therefore carry credential exposure risk. Existing API response sanitizers cover known task fields. |
| A03 | High financial impact | `app/controllers/v1/video.py:235` admits legacy generation tasks and `app/controllers/v1/llm.py` admits script operations without a persisted spending authorization tied to immutable inputs. There is no shared atomic budget guarantee across the legacy launchers. |
| A04 | Medium in shared deployments | Legacy auth uses one shared key. Task operations and artifact reads have no principal-specific project ownership boundary. The new workspace needs explicit owner checks and its own authentication. |
| A05 | Positive control | The prepared renderer passes argument arrays and inserts `-protocol_whitelist file,pipe` at its FFmpeg input boundaries (`app/services/video_project.py:496`). `app/utils/file_security.py` provides a resolved-path containment check and legacy media controllers validate upload filenames. No shell command injection was confirmed in the reviewed render path. |

## Open audit items

- Configurable legacy provider endpoints can target operator-specified hosts. No remote-user SSRF path was confirmed during this review. Safe defaults for the new workspace use fixed destinations, disabled redirects and metadata-only research links.
- The repository lock was exported for a PyPI advisory scan. Results and coverage limits are recorded in `SECURITY.md`. A Python advisory scan does not cover embedded FFmpeg or every native library.
- The repository license is MIT. Bundled fonts and tracks under `resource/` and assets from stock and AI providers have separate terms or provenance. The repository license alone does not establish permission to redistribute each asset or use it in a monetized video. The production workspace uses no bundled music by default.

## Missing production controls

Research provenance and review, immutable script and scene revisions, per-version approvals, scoped budgets and charges, owner-scoped workspaces, durable queue reconciliation, language-specific asset selection, and reviewed exports need a new persistence boundary. SQLite in WAL mode fits a local install and lets admission reserve funds and enqueue work within one transaction. Legacy Redis, TOML, task folders and prepared-media project manifests keep their existing formats.
