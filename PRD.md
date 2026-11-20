# Supervised documentary production

The production workspace serves a local editor creating documentary videos from sources, approved narration and visual scenes. Each project resumes from its last persisted version after restart.

The editor supplies research links and excerpts, reviews an analysis with source indices, and approves a script that distinguishes verified facts, assumptions, speculation, and uncertainty. Script revisions retain stable section keys so unrelated approved scenes survive. Storyboard cards expose prompts, reference images, camera direction, timing, style, and scene review.

Generation requires an explicit confirmation against a quote. An editor can stage multiple scenes in one quote, compare render attempts, revise a scene, and audit costs. The ledger separates reservations, actual charges, and unresolved charges. Provider account limits are administered with the provider.

The same visual clips support English and Brazilian Portuguese narration, mixed or single-language MP4 tracks, and embedded and downloadable SRT captions. The timeline supports clip selection, trimming, timing, fade-to-black, music, effects, and a watermarked draft. The final MP4 requires approval of an unwatermarked assembly.

Local composition and motion are usable without provider accounts. AI-generated imagery and advanced video require a configured OpenAI or Runway account. The delivered research workspace analyzes user-supplied sources. It has no autonomous source discovery, and claim verification remains the editor's responsibility.

## Hyphotenic studio experience goal

Deliver a leading interface for producing source-backed short documentaries. A person opening a project for the first time should be able to tell what is ready, what needs a decision, and how to continue. Guide them from topic and sources to an editable draft, preview, review, and export. Keep routine actions in the context of the current project or scene. Reveal advanced controls when relevant and give provider and account settings a dedicated place.

- **Desktop editing** keeps the video, timeline, and relevant scene controls within reach. Common revisions such as replacing a shot, trimming a scene, or editing a caption can be inspected in context before they are submitted. Make keyboard navigation and shortcuts discoverable.
- **Mobile review** supports watching the latest version, jumping to a scene, leaving a timestamped note or requesting a change, and approving the reviewed version. Validate touch controls and playback on real phones.
- **Project and job status** shows which revision is being viewed, whether edits are saved, and whether a job is queued, running, failed, or awaiting approval. Show the cost ceiling and what will be submitted before authorization. After a reload or recoverable failure, show how to resume and preserve the work already saved.
- **Accessibility and consistency** require clear hierarchy, readable states, semantic controls, keyboard operation, visible focus, alternatives to dragging for essential actions, and support for reduced motion. The core workflow must meet applicable level A and AA criteria. [WCAG 2.2](https://www.w3.org/TR/WCAG22/)

### Usability acceptance

Evaluate the published experience with E02 and F01. Give first-time users the outcome to reach and observe whether they can start a project, get to an editable preview, fix a scene, and export without step-by-step help. Ask reviewers on phones to find a scene, leave feedback, and approve the correct revision. Include a failed or interrupted job and verify that users can find the next safe action and resume. Check keyboard and screen-reader use on the same core flows.

Compare task completion without help, time to first editable preview, active editing time for a correction, navigation detours, and mistakes against the current studio and peer tools with equivalent workflows. Record render time separately from interaction time. Treat failures that prevent core task completion, loss of saved work, or ambiguous spending and approval states as release blockers for this goal. Use the observed results to substantiate any state-of-the-art claim for this workflow.
