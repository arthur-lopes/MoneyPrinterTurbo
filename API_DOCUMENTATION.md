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
| POST /api/projects/{project}/research | Save sources, claims and optional `editorial_focus` (question, proposed finding, angle and uncertainty). Sources contain title, URL and excerpt. Claims contain text, category and one-based source indices. Optional `If-Match` pins the quoted research version ID; `If-None-Match: *` requires a first save. Conflicts return 412. Success includes the new `ETag`. |
| POST /api/projects/{project}/script | Save script sections with key, title, narration, seconds, category and source indices. Optional `X-Research-Revision` pins the research used to select sources. Returns 409 if research changed. Optional `If-Match` pins the quoted script version ID, and `If-None-Match: *` requires a first save. Returns 412 for a conflicting script and the new `ETag` on success. |
| POST /api/projects/{project}/storyboard | Build affected scenes from the current approved script. |
| PUT /api/projects/{project}/scenes/{scene} | Save a new storyboard scene version. |
| PUT /api/projects/{project}/order | Reorder active scene IDs using `scenes`. |
| POST /api/projects/{project}/scenes/{scene}/split | Split using `seconds`, `first`, and `second`. |
| POST /api/projects/{project}/scenes/merge | Merge adjacent IDs from `scenes`. |
| POST /api/projects/{project}/scenes/{scene}/translation/{language} | Save title, description and narration for a translation. |
| POST /api/projects/{project}/reviews | Record `versions`, `status` and optional `comment`. |
| GET /api/projects/{project}/timeline | Read the current saved timeline, or pass `?revision=<id>` for an earlier revision. |
| PUT /api/projects/{project}/timeline | Save an editing draft, including clips, languages and output options. Returns the saved revision and its `ETag`. |
| POST /api/projects/{project}/timeline/cut-preview | Compile the submitted `timeline` for one of its `language` tracks. Returns ordered scene timings, caption cues and project-scoped media URLs for interactive playback. |
| GET /api/projects/{project}/proposals/{proposal}/preview?language=en-US | Preview a pending proposal as the project owner. Use `cut=saved` to play its saved base revision, or `cut=proposed` (default) to play the suggested changes. Both variants check the base revision and the proposal input fingerprint. Both return the cut preview format and leave the saved timeline, jobs and costs unchanged. |
| GET /api/projects/{project}/scenes/{scene}/evidence | Read the editorial categories and cited sources for the scene's recorded research revision. |
| GET /api/projects/{project}/delivery | Review the current saved cut, its scene timings, exact material versions and output settings. |
| GET /api/projects/{project}/exports/{version}/costs | Owner-only costs through an exact export. Amounts are integer minor units in the project currency. |
| POST /api/projects/{project}/exports/{version}/expenses | Record a paid external receipt for eligible media in the exact export. Send `provider`, `receipt`, integer `amount`, the version IDs in `materials`, `request_id` and `confirmed: true`. To correct a receipt, pass its latest record ID as `expected_version`. |
| GET /api/projects/{project}/external-expenses/{receipt}/history | Owner-only history of all versions of a receipt, using any of its record IDs. |
| POST /api/projects/{project}/delivery/materials | Approve the materials of selected `scenes` with the saved `revision`, matching `versions` and `confirmed: true`. |
| POST /api/projects/{project}/delivery/prepare | After material approval, save the unwatermarked cut with the same `revision`, all its material `versions` and `confirmed: true`. |
| GET, POST /api/projects/{project}/assemblies/{version}/notes | List timestamped review notes on one assembly or add `body`, integer `position_ms` and a `request_id`. |
| GET /api/projects/{project}/assemblies/{version}/notes/{note} | Read a note with its resolution and reopening history. |
| PUT /api/projects/{project}/assemblies/{version}/notes/{note}/state | Set `state` to `OPEN` or `RESOLVED` using the note's last `state_version` as `expected_version`. |
| POST /api/projects/{project}/quotes | Estimate one or more operations in `jobs` without queuing them. |
| POST /api/projects/{project}/quotes/{quote}/authorize | Confirm with `confirmed: true` and integer `maximum`. Repeating the same confirmation returns the same jobs. |
| GET /api/projects/{project}/quotes/{quote} | Read the archived job inputs, estimate, and authorization decision. |
| POST /api/projects/{project}/jobs/{job}/cancel | Cancel queued or running work. Remote cancellation can leave uncertain charges. |
| POST /api/projects/{project}/jobs/{job}/reconcile | Resume checking the saved Runway task. Repeating the request while the same job is active or complete returns success. A different active job for the asset or changed inputs returns a conflict. |
| POST /api/projects/{project}/jobs/{job}/settle | Record integer `actual`, `receipt`, and `confirmed: true`. Repeating the same verified amount and receipt returns success. The project snapshot includes the job's reservation and recorded settlement. |
| PUT /api/projects/{project}/budget | Set `scope`, optional `target`, integer `limit`, and `confirmed: true`. |
| GET, PUT /api/providers | Read capabilities and credential availability or configure API providers. The Codex read-only entry reports whether the local CLI is signed into ChatGPT. |
| POST /api/projects/{project}/uploads | Raw bytes. Use query `kind=reference\|image\|audio\|video\|voice` and `title`. Storyboard images, video, and voice use `scene`. Voice also uses `language`. For video prepared in Google Flow, pass `source_provider=google_flow`, `scene_version`, and `image_version` to record its declared source and require the same approved inputs. |
| GET, HEAD /api/projects/{project}/media/{version} | Owner-authenticated media, single byte range, optional `attachment=true`. |

Job operations are `research`, `script`, `image`, `video`, `translation`, `voice`, `rough_cut`, `assembly`, `export` and `analysis`. Include the scene ID for scene operations and the language for translation and voice. Pass the saved timeline ID as `version` for assembly or pass an inline `timeline`. Export takes the approved assembly ID as `version`. Validation errors return 422. Missing private resources return 404. Conflicting versions, approvals or budgets return 409. Schema definitions are in `app/production/models.py`.

Export costs include the project jobs created by the export timestamp. The same job set uses the latest verified charges, pending estimates and reservations, so a later receipt updates that export's report. The `materials` group follows the export's immutable version links and counts shared jobs once. The `other` group includes the remaining project work through that export. The response reports missing versions, missing ledger links, media with no linked job and subscription use. Approval refers to the source assembly's latest decision at the time of the export.

External expenses use the project currency and eligible media version IDs with no generation job. Each report counts a receipt once when any of its linked versions is used. The same receipt can appear in reports for different revisions. The latest receipt version contributes to `external.actual` and `confirmed_total`. Corrections append to history and a zero amount reverses a receipt. Repeating a successful `request_id` with the same details returns its original record. External expense records leave provider authorizations and budget reservations unchanged.

Set the project default `video` to `google_flow` or save a scene with `provider=google_flow` and `model=import`. Flow footage arrives through `/uploads` and uses the regular review and assembly workflow. Quoting automatic video generation for a Flow scene returns 409.

In the workspace, choose **Projects → New project → Project template → HYPHOTENIC** and select a series. The equivalent `POST /api/projects` request uses `channel: "hyphotenic"`, `series` (`what-if`, `alternate-history`, `first-24-hours`, or `shorts`), `public_title`, and `topic`. It accepts the optional `changed_condition`, `unchanged_conditions`, `scope`, and `sources`. Supplied sources become a research draft awaiting review. Optional `duration`, `structure`, `audience`, `tone`, `languages`, and individual `style` fields override inherited values. The project stores the chosen profile revision and plans in BRL.

HYPHOTENIC quotes with direct provider charges require the current script and all active storyboard scenes to be approved. Authorizing the quote requires a separately confirmed BRL project budget and quote maximum. If an approval input changes before submission, the queue cancels the pending job and releases its reservation. Approval for external publication is recorded manually after review of the final cut and packaging.

For the first edit in a project, quote `{"operation":"rough_cut","provider":"local","version":"<approved-script-id>"}` in `jobs` and authorize the quote with a maximum of zero. One durable job creates the storyboard, visual guides, system narration in the original language, captions, a saved timeline and a watermarked 720p assembly. The job publishes the edit and its assets together. An existing storyboard or a newer script supersedes a pending first cut. Rough-cut assets awaiting review can be reused in a watermarked assembly. Final delivery requires approved inputs and an approved unwatermarked assembly.

A script section can supply `evidence_graphic` with `layout` set to `sequence`, `alignment`, `coupling` or `routes`, exactly two `labels`, optional `annotation` and a `reveal_at` time in seconds within the section. The section must cite at least one entry in `sources`. First-cut rendering animates the highlight on a continuous shot and shows the source titles and an illustrative schematic label. See the [E02 diagram settings](docs/hyphotenic-e02-diagramas.json).

Copy the current timeline's `ETag` into `If-Match` when saving, or pass `If-None-Match: *` when creating the first timeline. A conflicting write returns 412. Repeating a save with the same document returns its existing revision. When an assembly uses a saved timeline, the quote, assembly and export reference that revision. A new save supersedes older quotes and queued assemblies for that timeline. Earlier media remains available from its version URL.

The cut preview compiles an unsaved or saved timeline in a read-only transaction. Send `{"timeline": <document>, "language": "pt-BR"}` with the editor's project-owner session. Scene starts and durations use seconds. Caption cues are relative to the scene start. Fetch the returned media URLs with the same session. Approve the immutable rendered assembly to authorize export.

The delivery screen reviews the storyboard, reference frames, takes and narration for the selected cut. It records the operator and source cut in material approval events. Delivery preparation binds the reviewed inputs to its saved revision, so changed inputs require a new preparation before rendering. The owner quotes and authorizes the unwatermarked assembly, reviews its MP4, then authorizes the export of that approved version.

Review notes stay on their rendered assembly, including when a later cut becomes current. Their `url` opens the exact version and timestamp. Repeat the same note request with its original `request_id` and fields to recover its receipt. Saving or resolving a note leaves the video, cost and approval state unchanged.

For a subtitle-only correction, set the scene's `clips[].captions` entry to a map such as `{"pt-BR": "Texto corrigido."}`. The existing audio and scene timing are reused. Request `?webvtt=true` on a caption media URL to load the same cues in a browser.

Use `provider: "codex"` for research, scripts, translations and editorial analysis. The quote marks the item `billing: "subscription"` and still requires confirmation. The monetary estimate covers direct API spend. ChatGPT manages its own usage allowance, credits, and automatic reload.

In **Assets and publishing → Experiments and performance**, choose a rendered assembly or final export and save `hypothesis`, `variable`, `control` and `primary_metric`. To record Studio data, choose the published export and supply its `youtube_id`, `days` (2, 7 or 28), `traffic`, `views` and relevant metric fields. The first observation freezes the hypothesis for that export. Corrections to a window and traffic source create new observation revisions.

Quote editorial analysis with `{"operation":"analysis","provider":"codex","version":"<rendered-version-id>"}` and include an `observation` ID to analyze a Studio sample. The analysis receives the versioned storyboard, narration and scene timings. It stores the report, hypothesis and observation IDs, and comparison used. It compares observations from the same owner's series, format, language, window and traffic within 20% of the selected runtime, up to 12 other distinct videos. The median and delta are observational. Studio figures are entered manually.

## YouTube connection for HYPHOTENIC

Use a Web application OAuth client in the `hyphotenic-moneyprinter` Google Cloud project. Enable YouTube Data API v3, YouTube Analytics API and YouTube Reporting API. Register the exact callback `https://hyphotenic.onboardingradar.com/api/youtube/callback`. Use `/youtube-about`, `/youtube-privacy` and `/youtube-terms` for public OAuth branding.

In **Canal do YouTube → Configurar cliente OAuth**, the authenticated operator can enter the Web client ID, secret and an optional expected channel ID. The server stores the credential encrypted under its workspace access token. Alternatively set `MPT_YOUTUBE_CLIENT_ID`, `MPT_YOUTUBE_CLIENT_SECRET`, `MPT_YOUTUBE_REDIRECT_URI` and a Fernet key in `MPT_YOUTUBE_TOKEN_KEY` in the server environment. In that mode use `MPT_YOUTUBE_CHANNEL_ID` to pin the channel. Server settings take precedence over the browser setup.

To rotate a workspace-managed secret, add a second secret to the same Google OAuth client and open **Canal do YouTube → Trocar segredo OAuth**. The workspace verifies the new secret against the connected channel before saving it and retains the existing refresh token and data. Disable the previous secret in Google after the workspace confirms the new one.

The operator authorizes the appropriate channel through Google. Read mode uses YouTube read and Analytics scopes. Publishing mode adds the scope needed by the video's upload, visibility, thumbnail and caption endpoints. In **Acervo e publicação → Publicação no YouTube**, choose the exact export and confirm its private upload. Wait for processing, then confirm assets, publication or a timezone-aware schedule. A failed final response requires a status check against the existing upload session or video ID. An interrupted or expired upload can be reconciled with an existing video from Studio.

The reporting windows count Pacific calendar dates from the published date. Imports show coverage, original views, watch metrics, traffic and retention points. Enabling Reach requests a daily report from the YouTube Reporting API. Official data stays in the YouTube panel. Use the separately entered experiment observations for the current Codex comparison workflow.

## Project agent API

The owner creates an expiring credential in **Project settings → Agent access**. `read` permits project context, media and review notes. `propose` also permits validating and submitting edit plans and reading that credential's receipts. Use `Authorization: Bearer <agent-token>` over HTTPS. The owner manages credentials independently of the project account.

| Method and path | Behavior |
| --- | --- |
| GET /api/agent/v1/projects/{project} | Read sources, script, saved timeline, scene IDs, eligible takes and the latest preview. |
| GET /api/agent/v1/projects/{project}/jobs | Read recent project jobs. Use `state=all`, `active`, `attention` (failed or billing review pending), or `completed`, and `limit` from 1 to 50 (default 20). |
| GET /api/agent/v1/projects/{project}/jobs/{job} | Read a project job and its exact result, with the owner review link. |
| GET /api/agent/v1/projects/{project}/scenes/{scene}/evidence | Read scene evidence with the project credential. The project context lists this URL for each scene. |
| GET, HEAD /api/agent/v1/projects/{project}/media/{version} | Read project media with the same project credential. |
| GET /api/agent/v1/projects/{project}/assemblies/{version}/notes | Read timestamped review notes for an assembly in the granted project, including the rendered scene and its relative timestamp when available. |
| POST /api/agent/v1/projects/{project}/proposals/validate | Compile the candidate against the current timeline and media approvals. Returns a diff and an input fingerprint. |
| POST /api/agent/v1/projects/{project}/proposals | Submit a validated edit for owner review. Include a unique `request_id` and optionally the validation's `fingerprint` as `expected_fingerprint`. Reuse both the request ID and the payload when retrying. |
| GET /api/agent/v1/projects/{project}/proposals/{proposal} | Read that credential's submission and the owner's decision. |

In the proposal, set `base_revision` to the ID returned by context and provide a `rationale`. Each entry in `changes` identifies a `scene` and may set `video`, `trim`, `seconds`, or `captions`. Caption entries use the language code and set text or null to restore narration captions. The response has a relative `review_url` for the owner. A changed base revision or changed inputs produces a conflict. Applying the proposal saves a new timeline revision. Rendering uses the existing owner quote and confirmation flow.

Each agent note returns a `scene` object with `id`, `title`, `start_ms`, `end_ms`, and `offset_ms`. It is null when the rendered scene cannot be identified. For a proposal that addresses a note, pass its ID in the optional `review_note` field. The note must belong to an assembly of the proposal's base revision with current inputs, and the proposal must edit the scene referenced by the note. The owner and the proposal receipt can open the original timestamp through `plan.review_note.url`.

Scene evidence includes `scene_version`, `script_version`, `research_version`, `fresh`, and each section's `category` and numbered sources. A `recorded` binding is stored with the scene. A `historical` binding uses matching script sections at the time an older scene was created. An `unavailable` binding returns no source links. The response follows the cited research revision even after a later research revision changes.

In the agent context, `script.research_revision` identifies the research revision used by the saved script. When it differs from the current research ID, `script_research` contains that earlier revision, including its sources and editorial focus. Otherwise use `research` when its ID matches the script binding. The focus records the editor's question, proposed finding, angle and limits. Claims and citations still require review.

The job response includes a relative `owner_url` for opening its card in Costs and a scoped `result.url` when the job produced media. The result identifies its version and whether it is current and fresh. Progress is present for active and completed jobs. Use `next_action` to monitor active jobs (`wait`, `wait_for_slot`) or notify the owner (`resume_provider_task`, `verify_charge`, `review_failure`, `review_result`). A job can also require billing review while another action is pending. Both agent scopes can inspect jobs. Generation, cancellation, provider task recovery and receipts use the owner interface.

The owner project snapshot exposes `jobs[].recovery_action` for failed Runway jobs with a recorded task. The values are `resume_provider_task`, `wait_for_slot`, and `inputs_changed`. Other jobs return null. The recovery endpoint rechecks the current inputs and slot before queuing the same task.

```powershell
# Set MPT_AGENT_TOKEN in the process environment from the issued credential.
python -m app.production.agent_cli --project-url $projectApi context
python -m app.production.agent_cli --project-url $projectApi jobs --state attention
python -m app.production.agent_cli --project-url $projectApi job $jobId
python -m app.production.agent_cli --project-url $projectApi evidence $sceneId
python -m app.production.agent_cli --project-url $projectApi notes $assemblyId
python -m app.production.agent_cli --project-url $projectApi validate edit.json
python -m app.production.agent_cli --project-url $projectApi submit edit.json
python -m app.production.agent_cli --project-url $projectApi status $proposalId
```
