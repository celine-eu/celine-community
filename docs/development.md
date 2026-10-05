# Development

The service listens on port `8019` and expects the shared PostgreSQL instance on port `15432`.

```bash
task setup
task test
task run
```

For the full local container flow:

```bash
docker compose up --build
```

The frontend always reads this BFF. Development authentication is disabled by default, so local
browser sessions validate the real Keycloak token and its per-REC role. Analytical data comes from
the Digital Twin configured by `DIGITAL_TWIN_API_URL` using the `svc-community` client credentials.
Set `DEV_AUTH_ENABLED=true` only for a deliberate fixture session; development authentication
is refused unless `CELINE_ENV=dev`.

## Real-token tests

`tests/test_real_tokens.py` checks the two grant levels — the `platform-admin` realm role and an
organization's own groups — against tokens minted by a **local** Keycloak whose realm
celine-policies has converged (`keycloak bootstrap`, `seed-dev-users`). It is skipped unless
`CELINE_COMMUNITY_KEYCLOAK_URL` is set:

```bash
CELINE_COMMUNITY_KEYCLOAK_URL=http://keycloak.celine.localhost .venv/bin/pytest tests/test_real_tokens.py
```

The legacy case — a token still carrying the retired realm group `/admins` — needs such a token
in `CELINE_COMMUNITY_LEGACY_TOKEN`, since a converged realm cannot mint one; without it that
case is skipped.

## Deployment posture

The service follows `celine.sdk.posture`: **only `CELINE_ENV=dev` relaxes** (`ENVIRONMENT` is
still read when `CELINE_ENV` is empty). Unset, empty, `staging`, `prod`, `test` or a typo is
hardened. `task run` and `task debug` export `CELINE_ENV=dev`; `CELINE_ENV=staging task run`
runs the same entry point hardened.

Hardened, startup refuses:

- a `DATABASE_URL` carrying a local-stack password;
- `CELINE_OIDC_CLIENT_SECRET` empty or equal to `CELINE_OIDC_CLIENT_ID` (the dev default);
- `CELINE_OIDC_BASE_URL` / `CELINE_OIDC_JWKS_URI` left on the SDK's local Keycloak default, or no
  audience;
- `DEV_AUTH_ENABLED=true`.

and the access policy fails closed: policies that do not load stop startup, and an evaluation
error denies. In dev the same findings are one warning at startup, and a missing policy degrades
to the fixture decision.

`celine.sdk.posture` ships in celine-sdk 2.0.0, the floor in `pyproject.toml`.
