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
| PUT /api/projects/{project}/defaults | Set preferred project providers for research, script, translation, images, new video scenes, and narration. |
| POST /api/projects/{project}/research | Save sources and claims. Sources contain title, URL and excerpt. Claims contain text, category and one-based source indices. |
| POST /api/projects/{project}/script | Save script sections with key, title, narration, seconds, category and source indices. |
| POST /api/projects/{project}/storyboard | Build affected scenes from the current approved script. |
| PUT /api/projects/{project}/scenes/{scene} | Save a new storyboard scene version. |
| PUT /api/projects/{project}/order | Reorder active scene IDs using `scenes`. |
| POST /api/projects/{project}/scenes/{scene}/split | Split using `seconds`, `first`, and `second`. |
| POST /api/projects/{project}/scenes/merge | Merge adjacent IDs from `scenes`. |
| POST /api/projects/{project}/scenes/{scene}/translation/{language} | Save title, description and narration for a translation. |
| POST /api/projects/{project}/reviews | Record `versions`, `status` and optional `comment`. |
| GET /api/projects/{project}/timeline | Read the current saved timeline, or pass `?revision=<id>` for an earlier revision. |
| PUT /api/projects/{project}/timeline | Save an editing draft, including clips, languages and output options. Returns the saved revision and its `ETag`. |
| GET /api/projects/{project}/delivery | Review the current saved cut, its scene timings, exact material versions and output settings. |
| POST /api/projects/{project}/delivery/materials | Approve the materials of selected `scenes` with the saved `revision`, matching `versions` and `confirmed: true`. |
| POST /api/projects/{project}/delivery/prepare | After material approval, save the unwatermarked cut with the same `revision`, all its material `versions` and `confirmed: true`. |
| POST /api/projects/{project}/quotes | Estimate one or more operations in `jobs` without queuing them. |
| POST /api/projects/{project}/quotes/{quote}/authorize | Confirm with `confirmed: true` and integer `maximum`. Repeating the same confirmation returns the same jobs. |
| GET /api/projects/{project}/quotes/{quote} | Read the archived job inputs, estimate, and authorization decision. |
| POST /api/projects/{project}/jobs/{job}/cancel | Cancel queued or running work. Remote cancellation can leave uncertain charges. |
| POST /api/projects/{project}/jobs/{job}/reconcile | Poll a failed Runway job using its stored task ID. |
| POST /api/projects/{project}/jobs/{job}/settle | Record integer `actual`, `receipt`, and `confirmed: true`. |
| PUT /api/projects/{project}/budget | Set `scope`, optional `target`, integer `limit`, and `confirmed: true`. |
| GET, PUT /api/providers | Read capabilities and credential availability or configure API providers. The Codex read-only entry reports whether the local CLI is signed into ChatGPT. |
| POST /api/projects/{project}/uploads | Raw bytes. Use query `kind=reference\|image\|audio\|video\|voice` and `title`. Storyboard images, video, and voice use `scene`. Voice also uses `language`. |
| GET, HEAD /api/projects/{project}/media/{version} | Owner-authenticated media, single byte range, optional `attachment=true`. |

Job operations are `research`, `script`, `image`, `video`, `translation`, `voice`, `rough_cut`, `assembly`, and `export`. Include the scene ID for scene operations and the language for translation and voice. Pass the saved timeline ID as `version` for assembly or pass an inline `timeline`. Export takes the approved assembly ID as `version`. Validation errors return 422. Missing private resources return 404. Conflicting versions, approvals or budgets return 409. Schema definitions are in `app/production/models.py`.

For the first edit in a project, quote `{"operation":"rough_cut","provider":"local","version":"<approved-script-id>"}` in `jobs` and authorize the quote with a maximum of zero. One durable job creates the storyboard, visual guides, system narration in the original language, captions, a saved timeline and a watermarked 720p assembly. The job publishes the edit and its assets together. An existing storyboard or a newer script supersedes a pending first cut. Rough-cut assets awaiting review can be reused in a watermarked assembly. Final delivery requires approved inputs and an approved unwatermarked assembly.

A script section can supply `evidence_graphic` with `layout` set to `sequence`, `alignment`, `coupling` or `routes`, exactly two `labels`, optional `annotation` and a `reveal_at` time in seconds within the section. The section must cite at least one entry in `sources`. First-cut rendering animates the highlight on a continuous shot and shows the source titles and an illustrative schematic label. See the [E02 diagram settings](docs/hyphotenic-e02-diagramas.json).

Copy the current timeline's `ETag` into `If-Match` when saving, or pass `If-None-Match: *` when creating the first timeline. A conflicting write returns 412. Repeating a save with the same document returns its existing revision. When an assembly uses a saved timeline, the quote, assembly and export reference that revision. A new save supersedes older quotes and queued assemblies for that timeline. Earlier media remains available from its version URL.

The delivery screen reviews the storyboard, reference frames, takes and narration for the selected cut. It records the operator and source cut in material approval events. Delivery preparation binds the reviewed inputs to its saved revision, so changed inputs require a new preparation before rendering. The owner quotes and authorizes the unwatermarked assembly, reviews its MP4, then authorizes the export of that approved version.

For a subtitle-only correction, set the scene's `clips[].captions` entry to a map such as `{"pt-BR": "Texto corrigido."}`. The existing audio and scene timing are reused. Request `?webvtt=true` on a caption media URL to load the same cues in a browser.

Use `provider: "codex"` for research, scripts, and translations. The quote marks the item `billing: "subscription"` and still requires confirmation. The monetary estimate covers direct API spend. ChatGPT manages its own usage allowance, credits, and automatic reload.

## Project agent API

The owner creates an expiring credential in **Project settings → Agent access**. `read` permits project context and media. `propose` also permits validating and submitting edit plans and reading that credential's receipts. Use `Authorization: Bearer <agent-token>` over HTTPS. The owner manages credentials independently of the project account.

| Method and path | Behavior |
| --- | --- |
| GET /api/agent/v1/projects/{project} | Read sources, script, saved timeline, scene IDs, eligible takes and the latest preview. |
| GET, HEAD /api/agent/v1/projects/{project}/media/{version} | Read project media with the same project credential. |
| POST /api/agent/v1/projects/{project}/proposals/validate | Compile the candidate against the current timeline and media approvals. Returns a diff and an input fingerprint. |
| POST /api/agent/v1/projects/{project}/proposals | Submit a validated edit for owner review. Include a unique `request_id` and optionally the validation's `fingerprint` as `expected_fingerprint`. Reuse both the request ID and the payload when retrying. |
| GET /api/agent/v1/projects/{project}/proposals/{proposal} | Read that credential's submission and the owner's decision. |

In the proposal, set `base_revision` to the ID returned by context and provide a `rationale`. Each entry in `changes` identifies a `scene` and may set `video`, `trim`, `seconds`, or `captions`. Caption entries use the language code and set text or null to restore narration captions. The response has a relative `review_url` for the owner. A changed base revision or changed inputs produces a conflict. Applying the proposal saves a new timeline revision. Rendering uses the existing owner quote and confirmation flow.

```powershell
# Set MPT_AGENT_TOKEN in the process environment from the issued credential.
python -m app.production.agent_cli --project-url $projectApi context
python -m app.production.agent_cli --project-url $projectApi validate edit.json
python -m app.production.agent_cli --project-url $projectApi submit edit.json
python -m app.production.agent_cli --project-url $projectApi status $proposalId
```
