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
- for a member whose registry status is `pending`, `suspended` or `inactive`, `PUT …/meter` and
  `PATCH …/members/{member_key}` answer `409 member_not_active` and write nothing (no write token is
  requested), while `DELETE …/meter` still detaches their meter; the members list carries each
  member's `status`, from which the dashboard decides which actions to offer
  ([ADR-0004](decisions/ADR-0004-a-name-meets-a-sensor-id-only-in-the-meter-dialog.md));
- a REC organization's `admins` or `managers` and a realm `/admins` member hold `members.meter` on
  that REC; a manager of another REC, a realm `/managers` badge and a service token holding
  `community.admin` are refused it. Without `REC_REGISTRY_ASSETS_WRITE_SCOPE` or a registry URL,
  `GET /api/me` does not report it;
- the BFF requests `rec-registry.assets.write` only for the meter write. Every meter press that reaches the
  registry writes one audit row naming the member key and the outcome, and neither that row nor any BFF log line holds
  a sensor id. The "Sent emails" view does not list meter presses;
- CSV and XLSX exports carry no meter flag and no sensor id;
- `PATCH …/members/{member_key}` with `{role: "prosumer"}` for a `consumer` answers `200 updated`
  and the members list then shows the new role; `{area}` with one of the REC's area keys does the
  same for the area, and the same values again answer `200 unchanged` and write nothing. A role other
  than `consumer` or `prosumer` answers `422 role_not_allowed` before the registry is asked; a role
  change for a `producer`, `operator` or `admin` answers `409 role_read_only`, while their area
  stays editable; an area that is not one of the REC's answers `422 unknown_area`. No other member
  field is accepted ([ADR-0003](decisions/ADR-0003-the-bff-writes-meter-role-and-area-to-the-registry-directly.md));
- `GET …/areas` lists the REC's area keys and names, with each area's boundary reference when the
  registry records one and its primary substation (`primarySubstation`, the area's first topology
  node id);
- `GET …/areas/shapes` (`community.read`) answers, for each of the REC's areas that references a
  boundary, `{areaKey, name, boundaryId, geometry}`, the geometry being the Digital Twin's
  `boundary_shape` GeoJSON, or `null` for an id the Digital Twin does not know or a boundary it
  cannot be asked for (a source outside its enum, an id over 64 characters: not sent, so the rest
  of the map still answers); an area without a
  boundary is not listed and a REC without boundaries asks the Digital Twin nothing. A Digital Twin
  outage answers `502 digital_twin_unavailable`, a refused Digital Twin token `502
  digital_twin_refused`, and neither is a `500` or cached; no shape or coordinate is logged. A
  manager of another REC is refused `403` before the registry or the Digital Twin is asked;
- a REC organization's `admins` or `managers` and a realm `/admins` member hold `members.edit` on
  that REC; a manager of another REC, a realm `/managers` badge and a service token holding
  `community.admin` are refused it. Without `REC_REGISTRY_PROFILE_WRITE_SCOPE` or a registry URL,
  `GET /api/me` does not report it;
- the BFF requests `rec-registry.members.profile.write` only for the profile write, and writes
  through the registry's profile route, never its general member `PATCH`. Every profile press that
  reaches the registry writes one audit row naming the member key, the outcome and the fields
  changed, with no name, email or address; a refused press records `changed: []` and `attempted`,
  each asked field's `{from, to}`; the "Sent emails" view does not list them;
- `GET …/alerts/audit-events` lists the meter and profile rows beside the alert, objective,
  feedback and email rows, and none of them holds a sensor id;
- the dashboard's members page shows "meter: yes / no" and never the id; a member approved through
  onboarding shows "meter: no". The meter dialog takes a typed sensor id of at most 122 characters
  and offers no list or suggestion of meters; it translates `sensor_held` and the other codes; a
  caller without `members.meter` sees no meter action, and no browser storage holds a sensor id;
- with `members.edit`, the dashboard's edit dialog moves a member's role between `consumer` and
  `prosumer` and changes their area from a select of the REC's areas; a `producer`, like an imported
  `operator` or `admin`, is shown read-only (area stays editable). Nothing is sent until the manager
  confirms a step that lists the changes and warns that both change the meter's rows from the next
  pipeline run and that a later full refresh rewrites history; a caller without `members.edit` sees
  no edit action. The dashboard offers detach for every member, and attach and the role/area
  edit only for active members;
- the edit dialog's area select names each area's primary substation, and below it a read-only map
  (celine-frontend `apps/community`, leaflet on OpenStreetMap tiles) draws the REC's areas from
  `GET …/areas/shapes`; nothing on the dashboard edits an area or a shape;
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

## Pending: the end-to-end run

**Status:** pending. The meter, role and area features
([ADR-0003](decisions/ADR-0003-the-bff-writes-meter-role-and-area-to-the-registry-directly.md),
[ADR-0004](decisions/ADR-0004-a-name-meets-a-sensor-id-only-in-the-meter-dialog.md)) are built and
listed in the checklist above: the meter routes and `members.meter`, the profile route, the areas
reads and `members.edit`, the dashboard's meter and edit dialogs and its read-only area map. These
items need a whole stack rather than this service alone: a REC registry at 1.6.0, onboarding's
`registry-sync`, the Digital Twin and the participant webapp. Each moves into the checklist in the
change that verifies it.

- a community with managers and no members opens with an empty members list, and every section
  still degrades to partial data rather than failing;
- the participant webapp tells a member approved through onboarding that they have no smart meter
  yet;
- the manager attaches a sensor that reported before the member onboarded, and within five minutes
  the member's webapp shows that meter's whole history; an attach of the same sensor in another REC
  answers `409 sensor_held`;
- the area map draws the shapes of a REC whose areas onboarding's `registry-sync` created from a
  template naming two primary substations.

Final acceptance of the area map also waits for the deployed Digital Twin `boundary_shape` fetcher
(with dataset-api's `st_asgeojson` and `st_simplify`) and for registry areas that carry a
boundary reference, which onboarding's `registry-sync` writes.
