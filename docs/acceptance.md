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
- a realm `/admins` badge lists every REC the registry knows, while a realm `/managers` badge grants
  no REC; managers must hold `/managers` inside the matching REC organization;
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
- `GET …/members` carries `hasMeter`, yes or no, for each member and never a sensor id; a member
  approved through onboarding answers `false`;
- `PUT …/members/{member_key}/meter` with a typed sensor id answers `201 attached`, and the list then
  answers `hasMeter: true`. The same id again answers `200 already_attached` and writes nothing. An id
  another active member holds, in this or another REC, answers `409 sensor_held` without naming a
  member or REC. The BFF reads no meter data other than that one member's meters
  ([ADR-0004](decisions/ADR-0004-a-name-meets-a-sensor-id-only-in-the-meter-dialog.md));
- an attach whose `meter-<id>` the member already holds for a different sensor id (imported data)
  answers `409 asset_key_taken` and replaces nothing; an id over 122 characters is refused before the
  registry is asked;
- `DELETE …/members/{member_key}/meter` with that id removes the meter, and the list answers
  `hasMeter: false` again;
- a REC organization's `admins` or `managers` and a realm `/admins` member hold `members.meter` on
  that REC; a manager of another REC, a realm `/managers` badge and a service token holding
  `community.admin` are refused it. Without `REC_REGISTRY_ASSETS_WRITE_SCOPE` or a registry URL,
  `GET /api/me` does not report it;
- the BFF requests `rec-registry.assets.write` only for the meter write. Every meter press that reaches the
  registry writes one audit row naming the member key and the outcome, and neither that row nor any BFF log line holds
  a sensor id. The "Sent emails" view does not list meter presses;
- CSV and XLSX exports carry no meter flag and no sensor id;
- the dashboard's members page shows "meter: yes / no" and never the id; a member approved through
  onboarding shows "meter: no". The meter dialog takes a typed sensor id of at most 122 characters
  and offers no list or suggestion of meters; it translates `sensor_held` and the other codes; a
  caller without `members.meter` sees no meter action, and no browser storage holds a sensor id;
- device, flexibility, points, nudging and alert flows remain usable at mobile and desktop widths;
- keyboard focus is visible, skip-to-content works, drawers expose dialog semantics, and reduced
  motion is respected;
- the feedback button is available on every authenticated manager page and successfully persists
  rating, comment, page diagnostics and an optional screenshot under the REC the page is for;
- the feedback inbox can explicitly switch between manager-dashboard and participant-dashboard
  feedback, lists only the authorized REC selected in the dashboard, and does not expose stored
  subject, IP or user-agent fields; both sources record monotonic `new` → `seen` → `resolved`
  transitions and manager-dashboard transitions also append this service's audit event;
- timeout or missing-fetcher scenarios degrade to partial data without blocking unrelated panels;
- alert acknowledge, mute and assign actions remain REC-scoped and appear in the audit log.

Core energy, meter, flexibility and points fetchers are deployed and have been exercised against
the local governed datasets. Administrative population is read from the REC Registry. Final
acceptance remains pending for objective actuals, pipeline status, nudging-event correlation,
anti-gaming and alert-ingestion sources listed in `downstream-integrations.md`, and for a session
with a real REC Manager.

## Planned: meter dialog, role and area

**Status:** planned. These items describe behaviour that is designed
([ADR-0003](decisions/ADR-0003-the-bff-writes-meter-role-and-area-to-the-registry-directly.md),
[ADR-0004](decisions/ADR-0004-a-name-meets-a-sensor-id-only-in-the-meter-dialog.md)) and not yet
built. Each moves into the checklist above in the change that makes it true. The meter routes and the
`members.meter` capability, the dashboard's meter dialog (celine-frontend `apps/community`) and its
"meter: no" for a member approved through onboarding are in the checklist already.

- a community with managers and no members opens with an empty members list, and every section
  still degrades to partial data rather than failing;
- the participant webapp tells a member approved through onboarding that they have no smart meter
  yet;
- with `members.edit`, a manager moves a member's role between `consumer` and `prosumer` and
  changes their area; a `producer`, like an imported `operator` or `admin`, is shown read-only, and
  the BFF refuses a profile write that sets any other role or changes such a member's role (area
  stays editable). The dialog warns that both change the meter's rows from the next pipeline run;
- the area select and a read-only map show the community's areas and their primary-substation
  boundaries; nothing on the dashboard edits an area or a shape;
- a REC organization's `admins` or `managers` and a realm `/admins` member hold `members.edit` on
  that REC; a manager of another REC, a realm `/managers` badge and a service token holding
  `community.admin` are refused it, and a caller without it sees no edit action;
- the BFF requests `rec-registry.members.profile.write` only for the profile write; every profile
  press that reaches the registry writes one audit row naming the member key and the outcome, and
  the "Sent emails" view does not list them.

Final acceptance of the area map also waits for the Digital Twin boundary fetchers and for
`st_asgeojson` and `st_simplify` in the dataset-api SQL allowlist.
