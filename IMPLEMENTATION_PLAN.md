# Implementation plan

1. Audit the existing source, dependency lock and provider contracts. Record security findings and identify reusable media components.
2. Add an opt-in loopback production workspace with project ownership, persistent revisions and explicit review events. Track narrowly scoped dependencies so unchanged scenes retain prior approvals.
3. Add immutable quotes, explicit maximum authorization, atomic job admission and reservations, and a ledger separating verified and uncertain charges.
4. Add offline media adapters and fixed-endpoint provider integrations. Record provider task IDs before polling and recover by status lookup where available.
5. Expose scene cards, storyboard and clip reviews, comparison, targeted regeneration and project progress.
6. Store language-specific narration, titles, descriptions and captions against shared visual assets.
7. Assemble and preview versioned timelines with FFmpeg, then publish only an approved export snapshot.
8. Verify workflow, security checks, restart recovery, renderer output and application startup. Documentation describes the delivered paths and remaining gaps.

The workspace now runs from `mpt production`. The focused production tests and offline end-to-end run exercised the approval and export sequence, including both system narration languages. Paid integrations use documented contracts with mocked verification. [Test coverage](TESTING.md) · [Security scope](SECURITY.md)
