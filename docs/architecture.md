# Architecture

`celine-community` is the standalone BFF for `celine-frontend/apps/community`, following the
same ownership boundary used by `celine-grid` and `celine-frontend/apps/grid`.

The browser calls only this BFF. The BFF evaluates local OPA policies for every
`(caller, action, REC)` triple, composes aggregate REC data from Digital Twin fetchers, and stores
only manager-owned workflow state in PostgreSQL.

## Which REC, and who says so

The token decides, and it decides per REC rather than once per session.

Groups exist at two levels and the difference is the whole boundary. The realm-level `/admins`
group (`groups` claim) is the only platform-wide human grant and reaches every REC on the
deployment. An **organization** group (`organization.<alias>.groups`) grants that REC only:
`/admins` and `/managers` work there only when the organization is typed `rec` — a Keycloak
organization is also how a DSO is modelled, and a DSO's managers are managers of a DSO. A
realm-level `/managers` group grants no dashboard data. `security/policy.py` reads the two levels
apart and passes only the organization matching the request; `celine.sdk.auth.jwt.extract_groups`
merges them, which would let a `managers` badge held in REC A authorise an action on REC B.

The **REC registry is the REC universe**: `GET /api/me` lists the registry's communities the caller
holds at least one capability on, so an organization alias the registry does not know is not
offered. The registry answers enumeration and naming only — every access decision is made from the
token with no network call, which is why a registry outage degrades the REC list and leaves
per-request access untouched. A caller whose grant is organization-level is then served from their
token with the REC names derived; a caller whose grant is realm-level gets `503`, because their list
has no other source and `403` would send them to look in the wrong place.

The organization **alias** is the REC's identity throughout: it is the Keycloak alias, the REC
registry's community key and the Digital Twin network id, one string rather than three. The
Keycloak UUID is parsed and unused.

Which REC is on screen is a path parameter, in the API and in the UI's `/[community]/...` routes.
Nothing derives it from the session.

Participant measurements and identity records are deliberately not persisted here. They remain
owned by the Digital Twin, dataset services, and REC Registry. Analytical surfaces accept only
aggregate series or device-level identifiers, and report missing downstream sources explicitly
through `partial` and `missingSources` in overview responses.

The one exception is the members surface. Participant **names** are read through from the REC
registry on each request, so a manager can find a person, and are not persisted, cached or logged
([ADR-0002](decisions/ADR-0002-members-by-name-from-the-registry-and-sends-through-onboarding.md)).
`members.read`, `members.invite`, `members.meter` and `members.edit` are person-only actions. None has a service
scope, and `community.admin` does not grant them. `members.invite` is reported by `GET /api/me` only when
`ONBOARDING_URL` is set. A send reaches the provisioning service only through onboarding, with this
BFF's token and the manager's forwarded token. The audit row names the member key, and the sends
view reads those rows back with names resolved at read time.

A manager attaches and detaches a member's meter under `members.meter`. The BFF writes the meter
asset to the REC registry directly, with a token asked for `rec-registry.assets.write` for the write
alone ([ADR-0003](decisions/ADR-0003-the-bff-writes-meter-role-and-area-to-the-registry-directly.md)).
A name meets a sensor id only in the meter dialog: the members list carries a yes/no meter flag, and
no audit row or log line holds a sensor id
([ADR-0004](decisions/ADR-0004-a-name-meets-a-sensor-id-only-in-the-meter-dialog.md)).
`members.meter` is reported by `GET /api/me` only when the registry URL and the write scope are
configured.

A manager corrects a member's role and area under `members.edit`, through the registry's profile
route, with a token asked for `rec-registry.members.profile.write` for the write alone (ADR-0003).
The dashboard sets a role of `consumer` or `prosumer` only, and the BFF refuses any other role, and
any role change for a member whose role is neither, before the registry is written; the area of
every member stays editable. `members.edit` is reported by `GET /api/me` only when the registry URL
and that scope are configured.

An attach and a profile edit are for active members only, and a detach for every member, so a
meter a suspended member still holds can be freed; the BFF refuses the first two with `409
member_not_active` before any registry write (ADR-0004). A refused profile edit's audit row records
what was attempted beside `changed: []`.

The read-only area map (`GET …/areas/shapes`, `community.read`) joins the registry's areas to the
Digital Twin's `boundary_shape`, the open reference boundaries of the primary substations. Nothing
here writes an area or a shape: areas are declared in onboarding's REC templates, each naming one
primary-substation boundary, and onboarding's `registry-sync` writes them and their topology node
to the registry.

The meter and profile presses are audited in the same `audit_events` table as the alert actions and
the email sends. The "Sent emails" view reads only the two email actions, so it never lists them;
the alerts audit feed lists every row of the REC.

The opt-in development profile uses a synthetic caller — `DEV_USER_PROFILE` selects an
organization-scoped manager or a realm admin, so both policy branches are exercisable without a
login — and deterministic overview data. Normal local operation validates Keycloak. Production
startup rejects the shortcut and policy failures are denied by default.

Operational monitoring composes `rec_meters_missing_intervals`, community points, engagement, and
pipeline-status fetchers into device and data-flow contracts. The join key is exclusively
`device_id`. Multiple gap rows are grouped per device and remain available in the technical
drill-down, while missing fetchers produce an explicit partial response.

Flexibility oversight composes `rec_flexibility_windows_history` and
`rec_flexibility_chain_daily`. The BFF calculates conversion and drop-off between stages and joins
window outcomes exclusively by `window_id` and `device_id`. Incomplete settlement, points, or
baseline correlations remain visible as partial data instead of being silently discarded. The
development adapter is deterministic and contract-compatible; production never falls back to it.

Gamification composes `rec_points_distribution`, `rec_points_leaderboard_community`,
`rec_anti_gaming_flags_community`, and the device points ledger. Nudging analytics read the
Nudging API's REC-scoped aggregate analytics endpoint; the BFF never exposes message bodies,
recipient addresses, or rule editing. Missing production sources yield explicit partial responses.

Manager alerts, acknowledgements and dashboard feedback are the analytical-adjacent workflows
persisted by this service. Alert reads and writes always include the JWT-derived REC boundary.
Acknowledge, mute, assign, and anti-gaming acknowledgement append immutable audit events with the
manager actor and technical resource reference. Feedback stores the authenticated manager and REC,
rating, comment, browser diagnostics and an optional screenshot. The browser names the REC the
feedback is about, because a manager may hold several; the BFF checks that claim against the policy
rather than trusting it. The manager inbox exposes only REC-scoped operational fields, serves
screenshots through a separate authenticated route, and records monotonic seen/resolved transitions
in the same audit log.

The same inbox also exposes participant-dashboard feedback as a distinct source. Those rows remain
owned by `celine-webapp`: this BFF first authorizes the selected REC, forwards the caller's verified
token to that service, and adapts its list, screenshot, and status routes to the manager UI's
same-origin contract. Keeping the sources separate preserves independent pagination and workflow
state and avoids runtime access to another service's database.
