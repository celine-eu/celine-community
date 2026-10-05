# Downstream integrations

The browser talks only to `celine-community`. In real-data mode the BFF reads governed aggregate
or device-keyed data through `celine.sdk.dt.DTClient`. The only participant identity it reads is a
member's name, from the REC registry, for the members list. It also reads whether a member holds a
meter, and writes meter assets and a member's role and area to the registry
([ADR-0003](decisions/ADR-0003-the-bff-writes-meter-role-and-area-to-the-registry-directly.md)).
Every call uses `DOWNSTREAM_TIMEOUT_SECONDS` (12 seconds by default). Unavailable or timed-out
fetchers produce `partial: true` and a `missingSources` entry instead of synthetic production data.

Responses are cached in process for `AGGREGATE_CACHE_TTL_SECONDS` (30 seconds by default), scoped
by REC, period and resource. The cache coalesces concurrent misses for the same key. Manager-owned
database workflows such as alert mutations and objective writes are not cached.

## Expected Digital Twin fetchers

| Surface | Fetcher |
| --- | --- |
| Energy | `rec_self_consumption`, `rec_self_consumption_daily` |
| Objective actuals | `rec_objective_progress_daily` |
| Population and meters | `rec_population_summary`, `rec_meters_health_summary` |
| Devices and data flow | `rec_meters_missing_intervals`, `rec_points_leaderboard_community`, `rec_device_streaks`, `rec_pipeline_status` |
| Flexibility | `rec_flexibility_windows_history`, `rec_flexibility_chain_daily` |
| Gamification | `rec_points_distribution`, `rec_points_leaderboard_community`, `rec_anti_gaming_flags_community`, `rec_device_points_ledger` |
| Nudging | Nudging API `GET /admin/analytics/communities/{id}/conversion` |
| Area map | `boundary_shape` (energy-community domain, open reference boundaries) |

The Digital Twin now implements the energy, monitored-population, meter-health/device, flexibility
and points fetchers in this table. They query governed datasets and return aggregates or technical
`device_id` values only. Objective targets are persisted by the BFF, while governed objective
actuals still require `rec_objective_progress_daily`. Administrative population is counted from
active REC Registry members inside the BFF; participant records are discarded immediately and only
the count enters the response.

The members list reads REC Registry `GET /admin/communities/{community_key}/members` (`list_members`,
`rec-registry.read`) once per request, one page, with the registry's own cursor. Only `key`, `name`,
`role`, `status` and `area` enter the response, and the delivery point count as a yes/no
`hasDeliveryPoint`; `user_id` and `did` are dropped in the BFF. Nothing is cached, because a name must not outlive the request. The meter flag
comes from `GET /admin/communities/{community_key}/meters` (`list_meters`, `rec-registry.read`),
at most ten pages of 500, of which only `owner_key` is kept. When it does not answer, the flag is
`null` and the list is still served.

The measurements dialog's read takes the member's `delivery_points` (`id` and `active` only) from
`get_member`, and each meter's `pod` from `list_meters?owner=`. A meter attach with a `pod` checks it
against those `delivery_points` (trimmed, case-insensitive) and writes the registry's spelling to
`properties.pod`; no registry route, scope or SDK call is added for it.

A meter attach or detach reads the member (`get_member`) and that member's meters
(`list_meters?owner=`) with the default token, then writes with a token from a second
client-credentials provider asking for `REC_REGISTRY_ASSETS_WRITE_SCOPE`
(`rec-registry.assets.write`, an optional scope of `svc-community`, never a default one, because the
Digital Twin forwards the default token). The attach is `put_asset`, a
`PUT /admin/communities/{c}/members/{m}/assets/meter-<trimmed id>` with `asset_type: meter`; the id
is capped at 122 characters so the key fits the registry's 128. When the member already holds that
key for a different sensor id, the BFF refuses `409 asset_key_taken` without calling `put_asset`,
since the upsert would replace that meter. The
detach is `delete_asset` of the asset the member holds with that id. The registry's refusal `code`
passes through, and its sentence is neither forwarded nor logged. There is no cache and no retry.
An attach to a member whose `status` is not `active` is refused `409 member_not_active` after
`get_member` and before any other call; a detach is not checked against the status.

The meter and profile writes need REC registry 1.6.0 or later and `celine-sdk` 1.21.0 or later
(`put_asset` reads the 1.6.0 asset `PUT` answer; `patch_member_profile` is the 1.6.0 profile route),
and the two optional scopes applied to the realm; the README's deployment order says what fails
otherwise.

A profile edit reads the member (`get_member`) with the default token, and, when the area changes,
the REC's areas (`get_community`, the keys of its `areas`), then writes with a token from a third
client-credentials provider asking for `REC_REGISTRY_PROFILE_WRITE_SCOPE`
(`rec-registry.members.profile.write`, an optional scope of `svc-community`, never a default one).
The write is `patch_member_profile`, a
`PATCH /admin/communities/{c}/members/{m}/profile` with only the fields that change, out of `role`
and `area`; it never uses the general member `PATCH`, which needs `rec-registry.members.write`. The
registry's refusal `code` passes through, and its sentence is neither forwarded nor logged. The
profile edit, too, is refused `409 member_not_active` after `get_member` for a member whose `status`
is not `active`. The areas route reads the same `get_community` with the default token and returns
each area's key, name, its `boundary` reference when the registry records one, and its first
`topology` node id as `primarySubstation`.

The area map (`GET …/areas/shapes`) reads `get_community` with the default registry token, then
calls the Digital Twin's `boundary_shape` value fetcher
(`POST /communities/it/{community_key}/values/boundary_shape`, payload `{source, ids}`, at most 100
ids per call, one call per boundary `source`) with the default Digital Twin token
(`DIGITAL_TWIN_SCOPE`, which includes `digital-twin.values.read`). The Digital Twin queries
dataset-api with its own service identity, because the boundaries are open reference data, so this
BFF needs nothing on dataset-api for the map. Each returned row's `geojson` string is parsed into
the response's `geometry`; an id the Digital Twin does not answer has `geometry: null`. A
boundary the fetcher's payload schema would refuse (a `source` outside its enum, today only
`gse_cabine_primarie`, or an id longer than 64 characters) is not sent, so one odd stored
reference cannot fail the whole map; its area has `geometry: null`. The answer
is cached like the aggregates. A Digital Twin that is unreachable or answers anything but a shape
list is `502 digital_twin_unavailable`; a `401`/`403`, or Keycloak refusing the token, is `502
digital_twin_refused`. Neither is cached.

Member emails go to onboarding, never to the provisioning service:
`POST /api/admin/communities/{community}/members/{member_key}/invitation|password-reset`, through
`celine.sdk.onboarding.OnboardingAdminClient`. The BFF's token comes from its own client-credentials
provider with `ONBOARDING_SCOPE` (`onboarding.members.invite`, an optional scope of `svc-community`).
The manager's token goes in `X-Acting-User-Token`, never in `x-auth-request-access-token`, which
onboarding refuses on these routes. There is no cache and no retry: sending again is the manager's
decision. A member release goes the same way, to
`POST /api/admin/communities/{community}/members/{member_key}/release`, with a token from a separate
provider asked for `ONBOARDING_RELEASE_SCOPE` (`onboarding.members.release`, an optional scope of
`svc-community`) only, so the email token can never end a membership. The installed `celine-sdk`
has no wrapper for it; `services/onboarding_release.py` makes the call with the SDK's header and
error type. The sends view resolves names for its page of rows from the same registry
`list_members` call, reading at most ten pages of 500.

Feedback from other browser applications remains in its owning service. The manager BFF proxies
participant feedback to `WEBAPP_API_URL` and ROI-calculator feedback to `ROI_API_URL`, always after
`CommunityReadDep` has authorized the requested REC. It forwards the browser's verified token, and
uses the same list, screenshot and monotonic status contract for both sources. No feedback table is
read across a service database boundary.

`rec_pipeline_status`, `rec_anti_gaming_flags_community`, and the
notification-event portion of the flexibility chain are not yet available. These gaps remain
visible as partial data. The BFF alert workflow is real and persistent, but its production alert
ingestion source is still to be connected.

The public contract is served at `/api/openapi.json` under `CELINE_ENV=dev` (or with
`CELINE_PUBLIC_DOCS=true`) and can be emitted deterministically with `task openapi` in any
environment. Operation IDs are stable and unique so downstream SDK generation does not depend
on router ordering.
