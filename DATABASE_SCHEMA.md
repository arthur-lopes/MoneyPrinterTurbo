# Production database

The workspace uses SQLite at `storage/production/production.sqlite3` by default. The schema is version 1. Back up the database and referenced assets together using a stopped service or a SQLite-aware backup. Legacy task folders and JSON manifests are read independently and can contribute prepared media through import.

| Table | Purpose |
| --- | --- |
| `projects` | Stable project UUID, owner, settings, languages, style, currency, and creation time. |
| `scenes` | Stable scene identities, sort positions and active flags. Split and merge retire earlier identities. |
| `versions` | Immutable per-slot sequence with document data, dependencies, optional media filename and job ID. Kinds cover research, script, scene, preview image, video, translation, voice, timeline, assembly, export and uploads. |
| `reviews` | Append-only actor, exact asset version, decision, comment and timestamp. |
| `providers` | Owner, provider, operation and currency settings. Credentials live in the process environment. |
| `budgets`, `budget_history` | Current scoped limits and explicit limit decisions. |
| `quotes`, `authorizations` | Immutable cost estimates, frozen generation inputs, confirmed maximum, operator and time. |
| `jobs` | Durable queue state, progress, provider task ID, attempts, cancellation, time and output version. |
| `ledger`, `settlements` | Quoted, reserved, actual and uncertain amounts and receipt-backed settlement events. |

Amounts use integer minor units. Calendar caps use the authorization date in UTC. Billing amounts depend on operator-entered evidence and can exceed the planning ceiling reported in a quote. SQLite foreign keys protect relational references and owner checks are enforced at the API and transaction boundaries.
