# Repository instructions

- Prioritize accuracy over agreement. Verify claims against the checkout and tests and distinguish verified results from estimates.
- Preserve existing MoneyPrinterTurbo entry points and artifact formats. Keep production changes scoped to the supervised workflow and documented interfaces.
- For production changes, verify the quote and authorization path, version dependencies, cancellation, and owner checks. Paid provider calls must remain mocked during development unless the user explicitly authorizes charges.
- Retain immutable versions, review events, and receipts. A paid submission with an unknown outcome requires reconciliation before another submission.
- Run the focused production suite and related legacy tests. Report unverified workflows and integration limits with the release or handoff.
