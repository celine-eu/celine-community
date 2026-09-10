# Architecture

`celine-community` is the standalone BFF for `celine-frontend/apps/community`, following the
same ownership boundary used by `celine-grid` and `celine-frontend/apps/grid`.

The browser calls only this BFF. The BFF derives exactly one `community_key` from the caller's
Keycloak organization, evaluates local OPA policies, composes aggregate REC data from Digital
Twin fetchers, and stores only manager-owned workflow state in PostgreSQL.

Participant measurements and identity records are deliberately not persisted here. They remain
owned by the Digital Twin, dataset services, and REC Registry. V1 accepts only aggregate series
or device-level identifiers and reports missing downstream sources explicitly through `partial`
and `missingSources` in overview responses.

The development profile uses one synthetic manager and deterministic overview data. Production
startup rejects both shortcuts and policy failures are denied by default.

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
rating, comment, browser diagnostics and an optional screenshot; the browser cannot choose the REC.
