## Repository role

This repository implements the CELINE REC Manager Dashboard backend-for-frontend.

The UI counterpart is `celine-frontend/apps/community`. It is a standalone application and must
not be merged into the participant-facing `celine-webapp` or `apps/webapp`.

## Boundaries

- Resolve one REC per authenticated organization in V1.
- Expose aggregate data and technical `device_id` values only; never resolve participant identity.
- Keep the BFF as the frontend's only access point to CELINE services.
- Put analytical aggregation in governed pipeline models and Digital Twin fetchers, not BFF SQL.
- Evaluate local OPA policies before every privileged or service-account operation.
- Keep onboarding, registry CRUD, nudging rule editing, multi-REC, Superset, billing, and grid-aware
  demand response out of V1.
