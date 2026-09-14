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
- `GET /api/communities/{community_key}/exports/{devices|flexibility|points|nudging|alerts}?format=csv|xlsx`

Every community route asserts that the path matches the single REC resolved from the authenticated
user's Keycloak organization. The API returns aggregates and `device_id` values, and member names on
the members list only.

The device board supports search, status/engagement filters, sorting, and pagination. Operational
responses expose `partial` and `missingSources` when a governed Digital Twin fetcher is not
available. Participant names are resolved from the REC registry for the members page and are not
persisted: no database row, no cache, and no name in a log line.

The members list (`members.read`) returns one REC registry page of `key`, `name`, `role`, `status`
and `area`, with the registry's cursor passed through. No address, account id, DID or delivery
point leaves the BFF. `q` narrows by name or key inside the page, because the registry has no text
filter. A name that only repeats the key is returned as `null`. `members.read` has no service
scope, and the `community.admin` superset does not grant it: names are for a person's screen. Errors
carry a machine-readable `detail.code`: `community_not_found` (`404`) or `registry_unavailable`
(`503`).

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

The local defaults enable a development identity without a Keycloak round trip. `DEV_USER_PROFILE`
picks which branch of the access policy it exercises: `manager` is an organization-scoped manager of
`gr-renewable-community`, `admin` is a realm admin who belongs to no organization and sees every REC
the registry lists. Both fixtures carry the claim shape a real token carries, so a fixture cannot
pass where production would deny. Analytical data always comes from the configured Digital Twin;
unavailable sources are returned as explicit partial data. Production startup refuses development
authentication.

The API listens on `http://localhost:8019`; OpenAPI is available at `/api/docs`.

## Tests

```sh
task test
task lint
task openapi > /tmp/celine-community-openapi.json
```
