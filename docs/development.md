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
cannot be enabled when `ENVIRONMENT=production`.
