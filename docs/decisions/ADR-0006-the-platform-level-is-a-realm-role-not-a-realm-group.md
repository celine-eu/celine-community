# ADR-0006 — The platform level is the realm role `platform-admin`, not a realm group

**Date:** 2026-10-03
**Status:** accepted

Narrows the realm-level grant that [ADR-0001](ADR-0001-the-rec-registry-is-the-rec-universe.md)
and [ADR-0004](ADR-0004-a-name-meets-a-sensor-id-only-in-the-meter-dialog.md) rely on; both
carry a dated 2026-10-03 amendment, and their decisions stand. Follows the platform
decision in celine-policies (ADR-0012 there, "a platform administrator is a realm role").

## Context

`community.rego` granted every action on every REC to the **realm** group `admins`
(`required_realm_groups`, `granted_by_realm_group`), read from `input.subject.groups`, beside
the per-REC grant of an organization's own `admins` and `managers`.

Realm groups were designed for one Keycloak per organization. The platform now runs many
organizations in one realm, and each has groups with **the same names**. A token therefore
carried `admins` at two levels with two meanings, and any reader that flattened the two
(the SDK's `extract_groups`) or guessed between `admins` and `/admins` let a community's own
administrators act as the platform's. This BFF also used "holds any group" as one of the
signals that a caller is a person.

## Decision

**Exactly two levels, and nothing in between.**

- **Platform:** the Keycloak realm **role** `platform-admin` (`realm_access.roles`). It is the
  only platform-wide grant: every known action on every REC (`granted_by_platform_role`,
  reason `granted by platform role`). It is a name no organization group carries.
- **Organization:** an organization's own `admins` and `managers`, for that organization's REC
  only, and only when the organization is typed `rec`.

**A realm group grants nothing.** `required_realm_groups` and `granted_by_realm_group` are
removed. `security/policy.py` passes the caller's realm roles as `input.subject.roles`, passes
`input.subject.groups` empty, and passes only the organization the request concerns; no rule
reads `input.subject.groups`. No other realm role grants anything.

**Person or service:** an organization membership makes the caller a person; otherwise the
SDK's `is_service_account` decides. Realm groups are no longer a signal.

No compatibility path is kept for the old realm groups; the realm converges to the new shape
and the change ships whole.

## Consequences

- **A platform administrator must hold the role.** A former realm `/admins` member without it
  lists no REC outside their own organizations and is refused elsewhere.
- **`GET /api/me` reports `platformRoles`** (the realm roles) instead of `realmGroups`. It is
  diagnostic; the frontend gates on the per-REC capabilities only.
- **The dev fixture** (`DEV_USER_PROFILE=admin`) carries `realm_access.roles: [platform-admin]`
  rather than a realm group.
- **The service needs celine-sdk 2.0.0** (`realm_roles`, `PLATFORM_ADMIN_ROLE`; `realm_groups`
  removed). The declared floor is raised when 2.0.0 is published.
- **Real-token tests** (`tests/test_real_tokens.py`) prove against a local Keycloak that an
  organization's `admins` is not a platform admin, the role holder is, and a realm group still
  present in a token grants nothing.
