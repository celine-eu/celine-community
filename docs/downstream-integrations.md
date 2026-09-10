# Downstream integrations

The browser talks only to `celine-community`. In real-data mode the BFF reads governed aggregate
or device-keyed data through `celine.sdk.dt.DTClient`; it never queries participant identity.
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
the count enters the response. `rec_pipeline_status`, `rec_anti_gaming_flags_community`, and the
notification-event portion of the flexibility chain are not yet available. These gaps remain
visible as partial data. The BFF alert workflow is real and persistent, but its production alert
ingestion source is still to be connected.

The public contract is available at `/api/openapi.json` and can be emitted deterministically with
`task openapi`. Operation IDs are stable and unique so downstream SDK generation does not depend
on router ordering.
