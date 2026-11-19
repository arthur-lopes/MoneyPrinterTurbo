# Production API

The launcher serves the workspace on `http://127.0.0.1:8765`. Use a same-origin authenticated browser session or `Authorization: Bearer <production-access-token>`. Owner identity comes from the server token mapping. Requests use JSON unless uploading binary data.

| Method and path | Behavior |
| --- | --- |
| GET /api/session | Reports whether the browser session is authenticated. |
| POST /api/session | Accepts `token` and sets a short-lived HTTP-only session cookie. |
| DELETE /api/session | Ends the current browser session. |
| GET, POST /api/projects | List or create projects. Create takes title, topic, audience, duration, tone, structure, languages, currency and optional style. |
| GET /api/projects/{project} | Project metadata, scene order, versions, reviews, costs and jobs. |
| PUT /api/projects/{project}/style | Save a project style guide and stale affected storyboard versions. |
| PUT /api/projects/{project}/defaults | Set preferred project providers for research, script, images, new video scenes, and narration. |
| POST /api/projects/{project}/research | Save sources and claims. Sources contain title, URL and excerpt. Claims contain text, category and one-based source indices. |
| POST /api/projects/{project}/script | Save script sections with key, title, narration, seconds, category and source indices. |
| POST /api/projects/{project}/storyboard | Build affected scenes from the current approved script. |
| PUT /api/projects/{project}/scenes/{scene} | Save a new storyboard scene version. |
| PUT /api/projects/{project}/order | Reorder active scene IDs using `scenes`. |
| POST /api/projects/{project}/scenes/{scene}/split | Split using `seconds`, `first`, and `second`. |
| POST /api/projects/{project}/scenes/merge | Merge adjacent IDs from `scenes`. |
| POST /api/projects/{project}/scenes/{scene}/translation/{language} | Save title, description and narration for a translation. |
| POST /api/projects/{project}/reviews | Record `versions`, `status` and optional `comment`. |
| PUT /api/projects/{project}/timeline | Save an editing draft, including clips, languages and output options. |
| POST /api/projects/{project}/quotes | Estimate one or more operations in `jobs` without queuing them. |
| POST /api/projects/{project}/quotes/{quote}/authorize | Confirm with `confirmed: true` and integer `maximum`. Repeating the same confirmation returns the same jobs. |
| GET /api/projects/{project}/quotes/{quote} | Read the archived job inputs, estimate, and authorization decision. |
| POST /api/projects/{project}/jobs/{job}/cancel | Cancel queued or running work. Remote cancellation can leave uncertain charges. |
| POST /api/projects/{project}/jobs/{job}/reconcile | Poll a failed Runway job using its stored task ID. |
| POST /api/projects/{project}/jobs/{job}/settle | Record integer `actual`, `receipt`, and `confirmed: true`. |
| PUT /api/projects/{project}/budget | Set `scope`, optional `target`, integer `limit`, and `confirmed: true`. |
| GET, PUT /api/providers | Read capabilities and credential availability or configure provider, operation, model, currency, rate ceiling, timeout, interval and enabled state. |
| POST /api/projects/{project}/uploads | Raw bytes. Use query `kind=reference\|audio\|video\|voice` and `title`. Video and voice use `scene`. Voice also uses `language`. |
| GET, HEAD /api/projects/{project}/media/{version} | Owner-authenticated media, single byte range, optional `attachment=true`. |

Job operations are `research`, `script`, `image`, `video`, `translation`, `voice`, `assembly`, and `export`. Include the scene ID for scene operations, language for translation and voice, timeline for assembly, and approved assembly version for export. Validation errors return 422. Missing private resources return 404. Conflicting versions, approvals or budgets return 409. Schema definitions are in `app/production/models.py`.
