# Production architecture

`mpt production` starts a loopback-only FastAPI service with a same-origin browser UI. The launcher, legacy API, Streamlit UI, one-click CLI and prepared-media project CLI retain their entry points. The workspace imports prepared media through a project-scoped interface.

The API passes schema-validated requests to `Workbench`. SQLite WAL handles short concurrent reads and serializes project mutations with `BEGIN IMMEDIATE`. The schema and job contract live under `app/production`. Each media file has a generated opaque name in a dedicated asset directory. Files are staged, normalized or rendered, and published before the owning version commits. A crash in the publication window can leave an unreferenced file, which has no media route.

An approval appends a review event. The latest version in a slot is its head. Dependency checks traverse approved heads, stable script section snapshots, scene narration snapshots, and storyboard ordering. Earlier versions remain browsable. Changes to voice selection or translation reuse visual clips.

Quotes freeze job inputs, dependency references, provider, model, configuration and cost. Authorization rechecks the quote and all applicable caps, then commits reservations and queue rows in a single write transaction. Workers recheck dependencies and provider availability before claiming. Results publish only while the original input snapshot remains valid.

The worker uses an OS-owned file lock. On restart, local work can resume, while a Runway job with a recorded provider task ID resumes polling. An interrupted paid submission without a task ID becomes unresolved. Providers are fixed-endpoint adapters with disabled redirects, explicit timeouts and bounded responses. OpenAI uses a single submission per attempt, and Runway polling can back off without another submission.

Timeline rendering shares the repository's FFmpeg packaging and subtitle file publication. The timeline uses normalized audio and video, language-specific tracks and a renderer dedicated to the project's timeline features. Native FFmpeg inputs use a local protocol allowlist and generated paths.
