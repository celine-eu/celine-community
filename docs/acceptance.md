# V1 acceptance checklist

Run the automated checks first:

```bash
task lint
task test
task openapi > /tmp/celine-community-openapi.json
pnpm --dir ../celine-frontend --filter @celine-eu/community test
pnpm --dir ../celine-frontend --filter @celine-eu/community check
pnpm --dir ../celine-frontend --filter @celine-eu/community build
```

For a real REC, disable development auth, configure the manager JWT, `svc-community` credentials
and Digital Twin URL, then verify:

- a manager can open only the REC resolved from their token; a different REC returns `403`;
- every percentage states its monitored denominator and partial sources remain visible;
- CSV and XLSX downloads match the active period and contain no participant identity;
- device, flexibility, points, nudging and alert flows remain usable at mobile and desktop widths;
- keyboard focus is visible, skip-to-content works, drawers expose dialog semantics, and reduced
  motion is respected;
- the feedback button is available on every authenticated manager page and successfully persists
  rating, comment, page diagnostics and an optional screenshot under the token-derived REC;
- timeout or missing-fetcher scenarios degrade to partial data without blocking unrelated panels;
- alert acknowledge, mute and assign actions remain REC-scoped and appear in the audit log.

Core energy, meter, flexibility and points fetchers are deployed and have been exercised against
the local governed datasets. Administrative population is read from the REC Registry. Final
acceptance remains pending for objective actuals, pipeline status, nudging-event correlation,
anti-gaming and alert-ingestion sources listed in `downstream-integrations.md`, and for a session
with a real REC Manager.
