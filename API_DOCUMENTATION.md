# Production API

The launcher serves the workspace on `http://127.0.0.1:8765`. Use a same-origin authenticated browser session or `Authorization: Bearer <production-access-token>`. Owner identity comes from the server token mapping. Requests use JSON unless uploading binary data.

| Method and path | Behavior |
| --- | --- |
| GET /api/session | Reports whether the browser session is authenticated. |
| POST /api/session | Accepts `token` and sets a short-lived HTTP-only session cookie. |
| DELETE /api/session | Ends the current browser session. |
| GET, POST /api/projects | List or create projects. Create accepts the standard project fields or the HYPHOTENIC profile request. |
| GET /api/project-profiles/hyphotenic | Read the built-in HYPHOTENIC base, script instructions and series profiles. |
| GET /api/project-profiles/hyphotenic/logo | Download the approved transparent Y logo. |
| GET /api/projects/{project} | Project metadata, scene order, versions, reviews, costs and jobs. |
| GET, PUT /api/projects/{project}/experiments/{version} | Read the hypothesis, observations, comparison and report for an exact rendered assembly or export. PUT saves the hypothesis. |
| POST /api/projects/{project}/experiments/{version}/observations | Save a versioned observation from YouTube Studio against a final export. |
| GET /api/youtube | Read the OAuth readiness and connected channel of the authenticated owner. |
| POST /api/youtube/setup | Save a Web OAuth client from the administrator's authenticated browser. |
| POST /api/youtube/connect | Begin the browser consent flow with `mode` set to `read` or `publish`. |
| GET /api/youtube/callback | Complete OAuth using the one-time state and the original operator session. |
| DELETE /api/youtube | Revoke the connection and remove associated imported metrics and remote video bindings. |
| POST /api/youtube/reach | Enable the daily YouTube Reach report with `confirmed: true`. |
| GET /api/projects/{project}/youtube/{export} | Read the exact export's status, receipt, events and imported reports. |
| POST /api/projects/{project}/youtube/{export}/upload | Authorize a private upload of the approved export. |
| POST /api/projects/{project}/youtube/{export}/{resume,cancel,refresh} | Resume or pause the upload, or reconcile the remote video status. |
| POST /api/projects/{project}/youtube/{export}/link | Link an existing video by `video` with confirmation and channel verification. |
| POST /api/projects/{project}/youtube/{export}/assets | Send an approved thumbnail or caption tracks from the export. |
| POST /api/projects/{project}/youtube/{export}/publish | Confirm public visibility or a scheduled timestamp for the exact video. |
| POST /api/projects/{project}/youtube/{export}/{metrics,reach}/{days} | Import Analytics or Reporting data for D2, D7 or D28. |
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
| GET, POST /api/projects/{project}/assemblies/{version}/notes | List timestamped review notes on one assembly or add `body`, integer `position_ms` and a `request_id`. |
| GET /api/projects/{project}/assemblies/{version}/notes/{note} | Read a note with its resolution and reopening history. |
| PUT /api/projects/{project}/assemblies/{version}/notes/{note}/state | Set `state` to `OPEN` or `RESOLVED` using the note's last `state_version` as `expected_version`. |
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

Job operations are `research`, `script`, `image`, `video`, `translation`, `voice`, `rough_cut`, `assembly`, `export` and `analysis`. Include the scene ID for scene operations and the language for translation and voice. Pass the saved timeline ID as `version` for assembly or pass an inline `timeline`. Export takes the approved assembly ID as `version`. Validation errors return 422. Missing private resources return 404. Conflicting versions, approvals or budgets return 409. Schema definitions are in `app/production/models.py`.

In the workspace, choose **Projects → New project → Project template → HYPHOTENIC** and select a series. The equivalent `POST /api/projects` request uses `channel: "hyphotenic"`, `series` (`what-if`, `alternate-history`, `first-24-hours`, or `shorts`), `public_title`, and `topic`. It accepts the optional `changed_condition`, `unchanged_conditions`, `scope`, and `sources`. Supplied sources become a research draft awaiting review. Optional `duration`, `structure`, `audience`, `tone`, `languages`, and individual `style` fields override inherited values. The project stores the chosen profile revision and plans in BRL.

HYPHOTENIC quotes with direct provider charges require the current script and all active storyboard scenes to be approved. Authorizing the quote requires a separately confirmed BRL project budget and quote maximum. If an approval input changes before submission, the queue cancels the pending job and releases its reservation. Approval for external publication is recorded manually after review of the final cut and packaging.

For the first edit in a project, quote `{"operation":"rough_cut","provider":"local","version":"<approved-script-id>"}` in `jobs` and authorize the quote with a maximum of zero. One durable job creates the storyboard, visual guides, system narration in the original language, captions, a saved timeline and a watermarked 720p assembly. The job publishes the edit and its assets together. An existing storyboard or a newer script supersedes a pending first cut. Rough-cut assets awaiting review can be reused in a watermarked assembly. Final delivery requires approved inputs and an approved unwatermarked assembly.

A script section can supply `evidence_graphic` with `layout` set to `sequence`, `alignment`, `coupling` or `routes`, exactly two `labels`, optional `annotation` and a `reveal_at` time in seconds within the section. The section must cite at least one entry in `sources`. First-cut rendering animates the highlight on a continuous shot and shows the source titles and an illustrative schematic label. See the [E02 diagram settings](docs/hyphotenic-e02-diagramas.json).

Copy the current timeline's `ETag` into `If-Match` when saving, or pass `If-None-Match: *` when creating the first timeline. A conflicting write returns 412. Repeating a save with the same document returns its existing revision. When an assembly uses a saved timeline, the quote, assembly and export reference that revision. A new save supersedes older quotes and queued assemblies for that timeline. Earlier media remains available from its version URL.

The delivery screen reviews the storyboard, reference frames, takes and narration for the selected cut. It records the operator and source cut in material approval events. Delivery preparation binds the reviewed inputs to its saved revision, so changed inputs require a new preparation before rendering. The owner quotes and authorizes the unwatermarked assembly, reviews its MP4, then authorizes the export of that approved version.

Review notes stay on their rendered assembly, including when a later cut becomes current. Their `url` opens the exact version and timestamp. Repeat the same note request with its original `request_id` and fields to recover its receipt. Saving or resolving a note leaves the video, cost and approval state unchanged.

For a subtitle-only correction, set the scene's `clips[].captions` entry to a map such as `{"pt-BR": "Texto corrigido."}`. The existing audio and scene timing are reused. Request `?webvtt=true` on a caption media URL to load the same cues in a browser.

Use `provider: "codex"` for research, scripts, translations and editorial analysis. The quote marks the item `billing: "subscription"` and still requires confirmation. The monetary estimate covers direct API spend. ChatGPT manages its own usage allowance, credits, and automatic reload.

In **Assets and publishing → Experiments and performance**, choose a rendered assembly or final export and save `hypothesis`, `variable`, `control` and `primary_metric`. To record Studio data, choose the published export and supply its `youtube_id`, `days` (2, 7 or 28), `traffic`, `views` and relevant metric fields. The first observation freezes the hypothesis for that export. Corrections to a window and traffic source create new observation revisions.

Quote editorial analysis with `{"operation":"analysis","provider":"codex","version":"<rendered-version-id>"}` and include an `observation` ID to analyze a Studio sample. The analysis receives the versioned storyboard, narration and scene timings. It stores the report, hypothesis and observation IDs, and comparison used. It compares observations from the same owner's series, format, language, window and traffic within 20% of the selected runtime, up to 12 other distinct videos. The median and delta are observational. Studio figures are entered manually.

## YouTube connection for HYPHOTENIC

Use a Web application OAuth client in the `hyphotenic-moneyprinter` Google Cloud project. Enable YouTube Data API v3, YouTube Analytics API and YouTube Reporting API. Register the exact callback `https://hyphotenic.onboardingradar.com/api/youtube/callback`. The workspace privacy disclosure is served at `/youtube-privacy`.

In **Canal do YouTube → Configurar cliente OAuth**, the authenticated operator can enter the Web client ID, secret and an optional expected channel ID. The server stores the credential encrypted under its workspace access token. Alternatively set `MPT_YOUTUBE_CLIENT_ID`, `MPT_YOUTUBE_CLIENT_SECRET`, `MPT_YOUTUBE_REDIRECT_URI` and a Fernet key in `MPT_YOUTUBE_TOKEN_KEY` in the server environment. In that mode use `MPT_YOUTUBE_CHANNEL_ID` to pin the channel. Server settings take precedence over the browser setup.

The operator authorizes the appropriate channel through Google. Read mode uses YouTube read and Analytics scopes. Publishing mode adds the scope needed by the video's upload, visibility, thumbnail and caption endpoints. In **Acervo e publicação → Publicação no YouTube**, choose the exact export and confirm its private upload. Wait for processing, then confirm assets, publication or a timezone-aware schedule. A failed final response requires a status check against the existing upload session or video ID. An interrupted or expired upload can be reconciled with an existing video from Studio.

The reporting windows count Pacific calendar dates from the published date. Imports show coverage, original views, watch metrics, traffic and retention points. Enabling Reach requests a daily report from the YouTube Reporting API. Official data stays in the YouTube panel. Use the separately entered experiment observations for the current Codex comparison workflow.

## Project agent API

The owner creates an expiring credential in **Project settings → Agent access**. `read` permits project context, media and review notes. `propose` also permits validating and submitting edit plans and reading that credential's receipts. Use `Authorization: Bearer <agent-token>` over HTTPS. The owner manages credentials independently of the project account.

| Method and path | Behavior |
| --- | --- |
| GET /api/agent/v1/projects/{project} | Read sources, script, saved timeline, scene IDs, eligible takes and the latest preview. |
| GET, HEAD /api/agent/v1/projects/{project}/media/{version} | Read project media with the same project credential. |
| GET /api/agent/v1/projects/{project}/assemblies/{version}/notes | Read timestamped review notes for an assembly in the granted project. |
| POST /api/agent/v1/projects/{project}/proposals/validate | Compile the candidate against the current timeline and media approvals. Returns a diff and an input fingerprint. |
| POST /api/agent/v1/projects/{project}/proposals | Submit a validated edit for owner review. Include a unique `request_id` and optionally the validation's `fingerprint` as `expected_fingerprint`. Reuse both the request ID and the payload when retrying. |
| GET /api/agent/v1/projects/{project}/proposals/{proposal} | Read that credential's submission and the owner's decision. |

In the proposal, set `base_revision` to the ID returned by context and provide a `rationale`. Each entry in `changes` identifies a `scene` and may set `video`, `trim`, `seconds`, or `captions`. Caption entries use the language code and set text or null to restore narration captions. The response has a relative `review_url` for the owner. A changed base revision or changed inputs produces a conflict. Applying the proposal saves a new timeline revision. Rendering uses the existing owner quote and confirmation flow.

```powershell
# Set MPT_AGENT_TOKEN in the process environment from the issued credential.
python -m app.production.agent_cli --project-url $projectApi context
python -m app.production.agent_cli --project-url $projectApi notes $assemblyId
python -m app.production.agent_cli --project-url $projectApi validate edit.json
python -m app.production.agent_cli --project-url $projectApi submit edit.json
python -m app.production.agent_cli --project-url $projectApi status $proposalId
```
