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
`area` and `hasMeter`, with the registry's cursor passed through. `hasMeter` is yes or no, never
which meter: it comes from the registry's meter list for the REC, of which only the owner keys are
kept, and is `null` when that list does not answer. No address, account id, DID, delivery point or
sensor id leaves the BFF. `q` narrows by name or key inside the page, because the registry has no text
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

**Attach meter** and **Detach meter** (`members.meter`, person-only like `members.read`, and granted
to the same groups) write the member's meter asset to the REC registry directly
([ADR-0003](docs/decisions/ADR-0003-the-bff-writes-meter-role-and-area-to-the-registry-directly.md),
[ADR-0004](docs/decisions/ADR-0004-a-name-meets-a-sensor-id-only-in-the-meter-dialog.md)). The
sensor id is typed as free text; nothing offers or suggests one. It travels in the body, never in a
path:

- `GET …/meter` answers `{memberKey, defaultMeterType, meters: [{sensorId, meterType}]}` for that
  one member, which is what the dialog shows.
- `PUT …/meter` takes `{sensorId, meterType?}`. `meterType` is one of `consumption`, `production`,
  `bidirectional`, `import` or `export`, and defaults from the role: `bidirectional` for a
  `prosumer`, `consumption` otherwise. The id is trimmed and written at `meter-<id>`; it is at most
  122 characters, so the key fits the registry's 128 (a longer one is a validation `422`). It answers
  `201 {outcome: "attached", sensorId, meterType}`, or `200` with `outcome: "already_attached"` when
  the member holds that id already; then nothing is written.
- `DELETE …/meter` takes `{sensorId}` and hard-deletes that one meter asset, leaving the member's
  others. It answers `204`.

A refusal is `{"detail": {"code"}}`: `409 sensor_held`, when another active member in any REC holds
the id, naming no one; `409 asset_key_taken`, when another member holds the key, or when this member
already holds `meter-<id>` for a different sensor id (imported data): that meter is not replaced and
nothing is written; `404 member_not_found`; `404 meter_not_found` on a detach; `422 sensor_id_blank`;
`422 asset_key_too_long`, the registry's code for a key over 128 characters, mapped although the cap
above keeps it from arising; `502 registry_unavailable`; `502 registry_refused`, when the registry
or Keycloak refuses this BFF's grant. Only the write uses a token asked for
`REC_REGISTRY_ASSETS_WRITE_SCOPE` (`rec-registry.assets.write`, an optional scope of
`svc-community`). The member and their meters are read with the default `rec-registry.read` token.
There is no cache and no retry. Without a registry URL or that scope the routes answer
`503 meter_writes_not_configured`, and `GET /api/me` does not report `members.meter`. Every press
that reaches the registry writes one `audit_events` row: `community.member.meter.attach` or `.detach`, resource
`registry_member`, the member key, and `{code, status}`. The row holds no sensor id, and neither
does any log line; `httpx`'s request log has the registry asset path redacted. The sends view does
not list these rows.

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

The local defaults validate the real Keycloak identity. Set `DEV_AUTH_ENABLED=true` only when a
deterministic fixture is explicitly wanted; `DEV_USER_PROFILE=manager` is an organization-scoped
manager of `example_rec`, while `admin` is a realm admin who belongs to no organization
and sees every REC the registry lists. Both fixtures carry the claim shape a real token carries.
Analytical data always comes from the configured Digital Twin; unavailable sources are returned as
explicit partial data. Production startup refuses development authentication.

The API listens on `http://localhost:8019`; OpenAPI is available at `/api/docs`.

## Tests

```sh
task test
task lint
task openapi > /tmp/celine-community-openapi.json
```
