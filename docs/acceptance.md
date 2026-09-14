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

- an organization-scoped manager sees exactly their own RECs in `GET /api/me`, and a REC they do
  not manage returns `403` whether or not it exists;
- a manager holding `managers` in one REC and a lesser group in another is refused the second one —
  the case a flattened group list used to allow;
- a realm `admins` or `managers` badge lists every REC the registry knows, including one whose
  Keycloak organization is missing or mistyped;
- a member of a Keycloak organization that is not typed `rec` is refused, and so is a member of one
  carrying no `type` at all;
- with one REC the dashboard opens straight into it; with several the picker appears, the choice is
  in the URL, and a reload or a shared link reopens the same REC;
- signing in with a valid token that grants nothing lands on `/denied` rather than looping through
  the login, and the REC registry being down reads as a temporary outage rather than a refusal;
- a section or action the caller has no capability for is absent from the UI rather than offered
  and then refused;
- every percentage states its monitored denominator and partial sources remain visible;
- CSV and XLSX downloads match the active period and contain no participant identity;
- the members page lists names and keys for the REC on screen only. A member whose registry name is
  their key shows the key with "no name on record". No name is stored in the database or written to
  the BFF log;
- a service token holding `community.admin` is refused `GET …/members`;
- with `ONBOARDING_URL` set, **Send invitation** on a member without a password delivers the email
  in the member's locale. **Reset password** on that member answers `no_password`. A second
  invitation within the cooldown answers `cooldown` with a time to retry. The dashboard translates
  each, and both this BFF's and onboarding's audit rows exist;
- with `ONBOARDING_URL` unset, the send buttons are absent;
- device, flexibility, points, nudging and alert flows remain usable at mobile and desktop widths;
- keyboard focus is visible, skip-to-content works, drawers expose dialog semantics, and reduced
  motion is respected;
- the feedback button is available on every authenticated manager page and successfully persists
  rating, comment, page diagnostics and an optional screenshot under the REC the page is for;
- timeout or missing-fetcher scenarios degrade to partial data without blocking unrelated panels;
- alert acknowledge, mute and assign actions remain REC-scoped and appear in the audit log.

Core energy, meter, flexibility and points fetchers are deployed and have been exercised against
the local governed datasets. Administrative population is read from the REC Registry. Final
acceptance remains pending for objective actuals, pipeline status, nudging-event correlation,
anti-gaming and alert-ingestion sources listed in `downstream-integrations.md`, and for a session
with a real REC Manager.
