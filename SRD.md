# Software requirements and behavior

| Capability | Implemented behavior |
| --- | --- |
| Project isolation | Owner-scoped project, workspace assets, approvals, jobs and media retrieval. |
| Review | Exact immutable versions support approval, rejection and revision requests. Edits create new versions. |
| Dependencies | Current approved research gates script. Approved sections and style gate the storyboard. Scene and first-frame approval gate video. Final assembly requires approved clips and narration. |
| Authorization | A quote expires after 15 minutes. Authorization carries an explicit maximum and queues one attempt per item. Reusing the same quote is idempotent. |
| Budget | Integer minor units. Project cap required for paid generation. Optional video, scene, provider, UTC day, and UTC month caps. One project represents one video with multiple language options. |
| Jobs | Durable SQLite queue, single worker per data directory, provider admission interval, cancellable local jobs, finite remote polling and reconciliation by recorded task ID. Ambiguous paid submissions require manual settlement or known-task reconciliation. |
| Media | PNG reference normalization, WAV audio, bounded MP4/MOV clip import, MP4 assembly with AAC and optional timed text. 4K requires suitable approved footage. |
| Languages | English US and Portuguese Brazil UI controls and narration workflows. Approved translation or original narration gates speech generation. Captions use approximate word grouping timed within the spoken clip. |
| Delivery | Same-origin browser preview and gated MP4 download. Title and description persist per language in delivery metadata returned by the API and shown in revision history. |

Recorded billing amounts depend on provider evidence. An operator-entered ceiling represents a spend reservation in MoneyPrinterTurbo. Provider-side account controls govern provider-side charging.
