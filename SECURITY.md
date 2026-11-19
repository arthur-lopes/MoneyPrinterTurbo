# Production security

Run the production workspace through `mpt production`. The service binds to IPv4 loopback and disables proxy headers. Browser sessions use HTTP-only, same-site cookies. State-changing cookie requests require the matching local origin. The service validates host headers, limits login and API traffic, and scopes every private resource by its authenticated owner. API access uses a bearer token.

Use an unshared random token and keep the workspace directory private to its OS account. The provider environment variables are `MPT_PRODUCTION_OPENAI_KEY` and `MPT_PRODUCTION_RUNWAY_KEY`. The workspace only returns whether a credential is available. When applicable, configure spending caps in provider dashboards. An operator can record verified charges that exceed an estimate or resolve an uncertain reservation using a receipt. [Runway usage limits](https://docs.dev.runwayml.com/usage/tiers/)

Agent credentials use the versioned project API and store only the digest of an expiring random token. They grant project reads and optional proposal submission. The owner retains decisions, timeline writes, job authorization and credential management. On public HTTPS, the proposal routes accept bearer-authenticated CLI requests with no browser origin. Browser operator actions continue to require their matching origin. Revocation prevents new authenticated requests and preserves the recorded proposals and decisions.

Research URLs are stored as references. The service makes network requests only to its fixed provider APIs and configured Runway output hosts. Provider redirects are disabled, and output hosts accept HTTPS only. Keep the output allowlist limited to verified provider CDN hosts. Uploads have byte and format limits and are normalized before publication. FFmpeg receives argument arrays and file-only input protocols. Native media processing runs with the workstation account's permissions.

The browser sends no telemetry. Project and source text sent to a paid provider is part of the confirmed job. Review who can read workstation files and the provider's data and licensing terms before using confidential material.

Codex subscription jobs require the local CLI's ChatGPT login. The workspace launches a bounded ephemeral CLI process from a temporary directory and excludes API keys, endpoint overrides, and user configuration. It disables shell, web search, plugins and other agent tools. Codex keeps ownership of its cached authentication. ChatGPT billing and automatic reload are account settings outside the workspace's monetary budget.

## Baseline scan and residual issues

The initial PyPI advisory scan found advisories in `multidict` 6.7.1 (PYSEC-2026-4244), `pyasn1` 0.6.4 (CVE-2026-119194 through CVE-2026-119197), `python-multipart` 0.0.30 (PYSEC-2026-3040), and `setuptools` 82.0.1 (PYSEC-2026-3447 and GHSA-xmj3-9gf7-h52w). The lock updates those packages to 7.2.1, 0.6.5, 0.0.31 and 84.2.1 respectively. A repeat PyPI scan of the default runtime export on Windows reported no known vulnerabilities on 2026-11-19. Native FFmpeg, OS packages, and optional install profiles need separate assessment.

The legacy entry points have their original execution and authentication model. The legacy API can bind to all interfaces and accepts unauthenticated requests when `app.api_key` is blank. Review [the baseline audit](ARCHITECTURE_AUDIT.md) before exposing any legacy endpoint. The supervised guarantees apply inside the production workspace.
