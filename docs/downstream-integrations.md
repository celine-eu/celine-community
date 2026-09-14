# Downstream integrations

The browser talks only to `celine-community`. In real-data mode the BFF reads governed aggregate
or device-keyed data through `celine.sdk.dt.DTClient`. The only participant identity it reads is a
member's name, from the REC registry, for the members list.
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

The Digital Twin now implements the energy, monitored-population, meter-health/device, flexibility
and points fetchers in this table. They query governed datasets and return aggregates or technical
`device_id` values only. Objective targets are persisted by the BFF, while governed objective
actuals still require `rec_objective_progress_daily`. Administrative population is counted from
active REC Registry members inside the BFF; participant records are discarded immediately and only
the count enters the response.

The members list reads REC Registry `GET /admin/communities/{community_key}/members` (`list_members`,
`rec-registry.read`) once per request, one page, with the registry's own cursor. Only `key`, `name`,
`role`, `status` and `area` enter the response; `user_id`, `did` and the delivery point count are
dropped in the BFF. Nothing is cached, because a name must not outlive the request.

Member emails go to onboarding, never to the provisioning service:
`POST /api/admin/communities/{community}/members/{member_key}/invitation|password-reset`, through
`celine.sdk.onboarding.OnboardingAdminClient`. The BFF's token comes from its own client-credentials
provider with `ONBOARDING_SCOPE` (`onboarding.members.invite`, an optional scope of `svc-community`).
The manager's token goes in `X-Acting-User-Token`, never in `x-auth-request-access-token`, which
onboarding refuses on these routes. There is no cache and no retry: sending again is the manager's
decision. The sends view resolves names for its page of rows from the same registry
`list_members` call, reading at most ten pages of 500.

`rec_pipeline_status`, `rec_anti_gaming_flags_community`, and the
notification-event portion of the flexibility chain are not yet available. These gaps remain
visible as partial data. The BFF alert workflow is real and persistent, but its production alert
ingestion source is still to be connected.

The public contract is available at `/api/openapi.json` and can be emitted deterministically with
`task openapi`. Operation IDs are stable and unique so downstream SDK generation does not depend
on router ordering.
