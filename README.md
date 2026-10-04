# CELINE Community

Standalone backend-for-frontend for the REC Manager Dashboard. The corresponding SvelteKit app is
`celine-frontend/apps/community`; it is separate from the participant webapp in the same way that
the Grid frontend and `celine-grid` backend form their own product surface.

## V0 API

- `GET /health`
- `GET /api/me`
- `GET /api/communities/{community_key}/overview?period=today|7d|30d`
- `GET /api/communities/{community_key}/objectives`
- `PUT /api/communities/{community_key}/objectives`
- `GET /api/communities/{community_key}/objectives/progress`
- `GET /api/communities/{community_key}/devices`
- `GET /api/communities/{community_key}/devices/{device_id}`
- `GET /api/communities/{community_key}/meters/{device_id}/gaps`
- `GET /api/communities/{community_key}/data-flow/pipelines`
- `GET /api/communities/{community_key}/flexibility/windows`
- `GET /api/communities/{community_key}/flexibility/windows/{window_id}`
- `GET /api/communities/{community_key}/flexibility/uptake`
- `GET /api/communities/{community_key}/demonstration/chain`
- `GET /api/communities/{community_key}/demonstration/windows/{window_id}`
- `GET /api/communities/{community_key}/demonstration/reach`
- `GET /api/communities/{community_key}/demonstration/summary`
- `GET /api/communities/{community_key}/points/distribution`
- `GET /api/communities/{community_key}/points/flags`
- `POST /api/communities/{community_key}/points/flags/{flag_id}/ack`
- `GET /api/communities/{community_key}/devices/{device_id}/points/ledger`
- `GET /api/communities/{community_key}/nudging/conversion`
- `GET /api/communities/{community_key}/alerts`
- `POST /api/communities/{community_key}/alerts/{alert_id}/ack|mute|assign`
- `GET /api/communities/{community_key}/alerts/audit-events`
- `GET /api/communities/{community_key}/members?q=&status=&cursor=&limit=`
- `PATCH /api/communities/{community_key}/members/{member_key}`
- `GET /api/communities/{community_key}/areas`
- `GET /api/communities/{community_key}/areas/shapes`
- `POST /api/communities/{community_key}/members/{member_key}/invitation|password-reset`
- `GET|PUT|DELETE /api/communities/{community_key}/members/{member_key}/meter`
- `GET /api/communities/{community_key}/members/sends?member_key=&actor=&intent=&code=&from=&to=&cursor=`
- `GET /api/communities/{community_key}/exports/{devices|flexibility|points|nudging|alerts}?format=csv|xlsx`

Every community route authorises the caller for the REC named in the path, from the token alone; a
caller may manage several RECs (see [`docs/architecture.md`](docs/architecture.md)). The API returns aggregates and `device_id` values, and member names on
the members list only.

The device board supports search, status/engagement filters, sorting, and pagination. Operational
responses expose `partial` and `missingSources` when a governed Digital Twin fetcher is not
available. Participant names are resolved from the REC registry for the members page and are not
persisted: no database row, no cache, and no name in a log line.

The members list (`members.read`) returns one REC registry page of `key`, `name`, `role`, `status`,
`area`, `hasMeter` and `hasDeliveryPoint`, with the registry's cursor passed through. `hasMeter` is
yes or no, never which meter: it comes from the registry's meter list for the REC, of which only the
owner keys are kept, and is `null` when that list does not answer. `hasDeliveryPoint` is yes or no,
never which delivery point (POD): the registry's delivery point count, greater than zero
([ADR-0005](docs/decisions/ADR-0005-a-manager-reads-a-members-delivery-point-in-the-measurements-dialog-only.md)).
No address, account id, DID, delivery point id or sensor id leaves the BFF in the list. `q` narrows by name or key inside the page, because the registry has no text
filter. A name that only repeats the key is returned as `null`. `members.read` has no service
scope, and the `community.admin` superset does not grant it: names are for a person's screen. Errors
carry a machine-readable `detail.code`: `community_not_found` (`404`) or `registry_unavailable`
(`503`).

**Send invitation** and **Reset password** (`members.invite`, person-only like `members.read`) go
through onboarding, which is the only caller of the provisioning service. The BFF sends its own
`svc-community` token with the `onboarding.members.invite` scope, and forwards the manager's token as
`X-Acting-User-Token`. Each route is one intent. A `200` is `{code, kind, lifespanSeconds}`. A refusal
is `{"detail": {"code"}}`, plus `retryAfterSeconds` and `Retry-After` on `cooldown`. Onboarding's
codes pass through unchanged. An onboarding `401`/`403` becomes `502 onboarding_refused`, and an
unreachable onboarding `503 onboarding_unavailable`. Without `ONBOARDING_URL` the routes answer
`503 onboarding_not_configured`, and `GET /api/me` does not report `members.invite`. Every press
that reaches onboarding writes one `audit_events` row (`community.member.invitation` or
`community.member.password_reset`, resource `registry_member`, the member key, and
`{code, kind, lifespan_seconds, status}`). The rows are the source of `…/members/sends`, which
resolves names at read time.

**Measurements**, **Attach meter** and **Detach meter** (`members.meter`, person-only like
`members.read`, and granted to the same groups) show a member's delivery points (POD, the DSO's
connection) read-only, and write the member's meter asset to the REC registry directly
([ADR-0003](docs/decisions/ADR-0003-the-bff-writes-meter-role-and-area-to-the-registry-directly.md),
[ADR-0004](docs/decisions/ADR-0004-a-name-meets-a-sensor-id-only-in-the-meter-dialog.md),
[ADR-0005](docs/decisions/ADR-0005-a-manager-reads-a-members-delivery-point-in-the-measurements-dialog-only.md)).
A POD is never written here: it is set and corrected through onboarding. The sensor id is typed as
free text; nothing offers or suggests one. The sensor id and the POD travel in bodies, never in a
path:

- `GET …/meter` answers `{memberKey, defaultMeterType, deliveryPoints: [{id, active}], meters:
  [{sensorId, meterType, pod}]}` for that one member, whatever their status, which is what the
  measurements dialog shows. `id` is the delivery point id as the registry spells it; nothing else of
  a delivery point (address, tariff) is returned. `pod` is the delivery point the meter is linked to,
  or `null`.
- `PUT …/meter` takes `{sensorId, meterType?, pod?}`. `pod`, optional, links the meter to one of the
  member's delivery points: it must be one the member holds, compared trimmed and
  case-insensitively, else `422 pod_not_held` and nothing is written; `properties.pod` then holds the
  registry's own spelling of it. Without `pod` (or with a blank one), none is written. `meterType` is one of `consumption`, `production`,
  `bidirectional`, `import` or `export`, and defaults from the role: `bidirectional` for a
  `prosumer`, `consumption` otherwise. The id is trimmed and written at `meter-<id>`; it is at most
  122 characters, so the key fits the registry's 128 (a longer one is a validation `422`). It answers
  `201 {outcome: "attached", sensorId, meterType}`, or `200` with `outcome: "already_attached"` when
  the member holds that id already; then nothing is written. Only an `active` member gets a meter:
  for any other status the attach answers `409 member_not_active` and writes nothing.
- `DELETE …/meter` takes `{sensorId}` and hard-deletes that one meter asset, leaving the member's
  others. It answers `204`. It is open for every member, whatever their status, so a manager can
  free a meter a suspended or inactive member still holds.

A refusal is `{"detail": {"code"}}`: `409 sensor_held`, when another active member in any REC holds
the id, naming no one; `409 member_not_active` on an attach; `422 pod_not_held` on an attach whose
`pod` is not one of the member's delivery points; `409 asset_key_taken`, when another
member holds the key, or when this member
already holds `meter-<id>` for a different sensor id (imported data): that meter is not replaced and
nothing is written; `404 member_not_found`; `404 community_not_found`; `404 meter_not_found` on a
detach; `422 sensor_id_blank`; `422 asset_key_too_long`, the registry's code for a key over 128
characters, mapped although the cap above keeps it from arising; any other registry validation
refusal with its own code, or `422 meter_rejected` when it has none; `502 registry_unavailable`;
`502 registry_refused`, when the registry or Keycloak refuses this BFF's grant. Only the write uses a token asked for
`REC_REGISTRY_ASSETS_WRITE_SCOPE` (`rec-registry.assets.write`, an optional scope of
`svc-community`). The member and their meters are read with the default `rec-registry.read` token.
There is no cache and no retry. Without a registry URL or that scope the routes answer
`503 meter_writes_not_configured`, and `GET /api/me` does not report `members.meter`. Every press
that reaches the registry writes one `audit_events` row: `community.member.meter.attach` or `.detach`, resource
`registry_member`, the member key, and `{code, status}`. The row holds no sensor id or POD, and
neither does any log line; `httpx`'s request log has the registry asset path redacted. The sends view does
not list these rows.

**Edit** (`members.edit`, person-only like `members.read`, and granted to the same groups) corrects
a member's role and area in the REC registry directly, through the registry's profile route
(`PATCH /admin/communities/{c}/members/{m}/profile`, `{role?, area?}` and nothing else;
[ADR-0003](docs/decisions/ADR-0003-the-bff-writes-meter-role-and-area-to-the-registry-directly.md)):

- `PATCH …/members/{member_key}` takes `{role?, area?}`, at least one, and no other key (an
  unknown key is a validation `422`). `role` is `consumer` or `prosumer`: settlement counts a
  meter's production only for a `prosumer`, so those two are the only roles the dashboard sets. A
  member whose role is neither (`producer`, an imported `operator` or `admin`) keeps it, and their
  area stays editable. `area` is a key of the REC's areas. Only what differs from the member's
  current values is sent. It answers `200 {outcome: "updated", memberKey, role, area, changed}`, or
  `200` with `outcome: "unchanged"` and `changed: []` when the member already has what was asked;
  then nothing is written. Only an `active` member is edited; the members list carries each
  member's `status`, so the dashboard offers the edit (and the attach) for active members only.
- `GET …/areas` (`community.read`) answers `{communityKey, areas: [{key, name, boundary,
  primarySubstation}]}` from the registry, for the dialog's area select. `boundary` is `{source,
  id}`, the primary-substation boundary the area references, and `null` while the registry records
  none. `primarySubstation` is the area's first topology node id, the substation the pipelines
  attribute the area's members to (equal to `boundary.id` once the area references a boundary), or
  `null` when the area lists no node.
- `GET …/areas/shapes` (`community.read`) answers `{communityKey, areas: [{areaKey, name,
  boundaryId, geometry}]}` for the read-only area map: one entry per area that references a
  boundary, in area-key order. `geometry` is a GeoJSON geometry object from the Digital Twin's
  `boundary_shape` (simplified for display), or `null` when the Digital Twin has no shape for that
  id. The areas are read from the registry with the default token and the shapes from the Digital
  Twin with the default Digital Twin token; the answer is cached for `AGGREGATE_CACHE_TTL_SECONDS`.
  Refusals: `404 community_not_found`, `502 registry_unavailable`, `502 registry_refused`, `502
  digital_twin_unavailable`, `502 digital_twin_refused`, `503 digital_twin_not_configured`. A
  boundary shape is open reference data: no member is named, and no shape or coordinate is
  logged.

A refusal is `{"detail": {"code"}}`. Before the registry is asked anything, and with no audit row:
`422 profile_empty`, `422 role_not_allowed` (a role other than `consumer` or `prosumer`), `422
unknown_area` (a blank area). After the member is read: `409 role_read_only` (a role change for a
member whose role is neither), `409 member_not_active` (the member's status is not `active`), `422
unknown_area` (not one of the REC's areas). From the registry,
unchanged: `422 invalid_role`, `422 unknown_area`, `404 member_not_found`, `404
community_not_found`; an uncoded validation refusal is `422 profile_rejected`. `502
registry_unavailable`; `502 registry_refused`, when the registry or Keycloak refuses this BFF's
grant. Only the write uses a token asked for `REC_REGISTRY_PROFILE_WRITE_SCOPE`
(`rec-registry.members.profile.write`, an optional scope of `svc-community`); the member and the
areas are read with the default `rec-registry.read` token. There is no cache and no retry. Without a
registry URL or that scope the route answers `503 profile_writes_not_configured`, and `GET /api/me`
does not report `members.edit`. Every press that reaches the registry writes one `audit_events` row:
`community.member.profile`, resource `registry_member`, the member key, and `{code, status,
changed}` plus `{from, to}` for each changed field. A refused press records `changed: []` and
`attempted`, `{from, to}` for each field it asked to change (`from` is `null` when the member could
not be read). A role and an area key are administrative
values; no name, email or address is in the row or in a log line. The sends view does not list
these rows.

The flexibility surface composes window history with the observed pathway `offered → nudged →
read → opened → committed → delivered → points`. It exposes drop-off, delivery against baseline,
correlation quality, and a device-only drill-down. The API describes observed associations and
does not claim causality or additionality.

Gamification responses declare the monitored-device denominator, include zero-point devices in
the distribution, and expose leaderboard, anti-gaming flags, and point ledgers only by
`device_id`. Nudging is a read-only aggregate surface: rules, thresholds, templates, recipients,
and message bodies cannot be changed or retrieved through this BFF.

The alert inbox is BFF-owned. Acknowledge, mute, assign, and anti-gaming acknowledgement require
`community.alerts.write`; every effective mutation creates an `audit_events` record scoped to the
same REC. Repeated acknowledgement is idempotent.

`GET …/alerts/audit-events` (`alerts.read`) returns the REC's latest 100 `audit_events` rows of every
kind, each with its `detail`: alert and anti-gaming actions, objective updates, feedback
transitions, email sends, meter presses and profile edits alike. It is the only view that lists meter and profile rows; the
"Sent emails" view (`…/members/sends`) reads invitation and password-reset rows only.

Aggregate reads use a short, REC-scoped in-process cache and a bounded downstream timeout. Digital
Twin failures are isolated as partial responses. Authorized table exports are available as CSV or
XLSX, protect spreadsheet cells from formula injection, and contain only aggregate or technical
device data. See [`docs/downstream-integrations.md`](docs/downstream-integrations.md) for the real
fetcher contract and [`docs/acceptance.md`](docs/acceptance.md) for the release checklist.

## Local development

```sh
cp .env.example .env
task setup
task alembic:upgrade
task run
```

The local defaults validate the real Keycloak identity. Set `DEV_AUTH_ENABLED=true` only when a
deterministic fixture is explicitly wanted; `DEV_USER_PROFILE=manager` is an organization-scoped
manager of `example_rec`, while `admin` holds the `platform-admin` realm role, belongs to no
organization and sees every REC the registry lists. Both fixtures carry the claim shape a real token carries.
Analytical data always comes from the configured Digital Twin; unavailable sources are returned as
explicit partial data.

Only `CELINE_ENV=dev` accepts the local defaults (`task run` exports it; `.env.example` sets it).
Unset or any other value is hardened: the dev database password, a client secret equal to the
client id, the SDK's default issuer and `DEV_AUTH_ENABLED` refuse to start, and the access policy
fails closed. See [docs/development.md](docs/development.md#deployment-posture).

The API listens on `http://localhost:8019`; OpenAPI is available at `/api/docs`.

## Deployment order

The member writes cross three services, so the order matters on every deployment:

1. **REC registry 1.6.0 or later.** It carries the profile route
   (`PATCH …/members/{m}/profile`), the coded refusals (`{detail, code}`, with `sensor_held`,
   `invalid_role`, `unknown_area` and the others this BFF passes through) and the asset `PUT`
   answer that `celine-sdk` 1.21.0 expects. Against an earlier registry an attach or an edit
   answers a `502`.
2. **The realm's clients.** `svc-community` must hold `rec-registry.assets.write` and
   `rec-registry.members.profile.write` as optional scopes (celine-policies `clients.yaml`, applied
   with `celine-policies keycloak sync`). Without them Keycloak refuses the write token and every
   press answers `502 registry_refused`. Setting `REC_REGISTRY_ASSETS_WRITE_SCOPE` or
   `REC_REGISTRY_PROFILE_WRITE_SCOPE` empty turns that action off instead.
3. **This BFF**, built with `celine-sdk>=1.21.0`.

The area map also needs the Digital Twin's `boundary_shape` fetcher (dataset-api with
`st_asgeojson` and `st_simplify`), and registry areas that reference a boundary. Areas are declared
in onboarding's REC templates and written to the registry by onboarding's `registry-sync`; until
then the map has nothing to draw and the area select still works.

## Tests

```sh
task test
task lint
task openapi > /tmp/celine-community-openapi.json
```
