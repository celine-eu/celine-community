# ADR-0001 — The REC registry answers which RECs exist, and the BFF holds no Keycloak Admin interface

**Date:** 2026-09-11
**Status:** accepted

## Context

`GET /api/me` must answer "which RECs may this caller open". For a caller whose grant is an
organization membership the token answers it. For a caller holding a **realm** `admins` or
`managers` group the token cannot: it names only the organizations they belong to, and a
platform administrator belongs to none. Their list has to come from somewhere else.

Two sources could supply it, and they do not agree.

**The REC registry** already exposes `GET /admin/communities`, and this service already
holds a `RecRegistryAdminClient` with the `rec-registry.read` scope. No new downstream, no
new credential.

**Keycloak's organizations** would be the other source, and the only way to detect a
registry community whose organization is missing or mistyped. It needs
`GET /admin/realms/{realm}/organizations`, which is a realm-management call. There is no
Keycloak admin client in `celine-sdk`; the registry's `CommunityListItem` carries no
organization field, so the join cannot be made registry-side; and `celine-policies`'
`AdminPermissions` model has **no field that can express a realm-wide grant** — its
docstring and [ADR-0003 there](../../../celine-policies/docs/decisions/ADR-0003-declare-realm-administration-as-a-group-scoped-permission.md)
say so deliberately, and only `celine-admin-cli` holds realm-management today. Supplying
`svc-community` with `view-organizations` would mean reopening that decision, adding a
BFF → Keycloak Admin seam, and carrying a grant that no provisioning run reproduces until
the model changes.

## Decision

**The REC registry is the REC universe, and the caller's grants are matched against it.**
A realm-level grant lists every registry community; an organization-level grant lists the
registry communities whose key equals one of the caller's REC-typed organization aliases.
An alias the registry does not know is not offered.

**The BFF makes no Keycloak Admin call.** It reads identity from the token and nothing
else.

The registry answers enumeration and naming only. Every `(caller, action, REC)` decision is
made from the token by `policies/community.rego`, in process, with no network call.

## Consequences

A registry community whose Keycloak organization is missing or mistyped **is listed** to a
realm administrator. That is the intended direction: a half-provisioned REC stays visible to
the only people who can repair it, rather than disappearing from the one console that would
have shown the fault.

A registry outage degrades the REC *list* and leaves per-request access untouched. An
organization-scoped caller is then served from their token with REC names derived from their
keys, and the response is marked `registryAvailable: false`. A caller whose only grant is a
realm group gets `503` rather than `403`, because their list has no other source and a `403`
would tell an administrator they have no access when a downstream is down.

A realm administrator asking for a `community_key` that exists nowhere is **not** refused:
their grant is deliberately organization-blind, and rejecting an unknown key would need a
registry lookup on every request rather than on enumeration. They receive the same empty,
`partial: true` answer any REC with no data produces. An organization-scoped caller asking
for a REC that is not theirs is refused identically whether or not it exists.

What will tempt someone to undo this is the orphan case — wanting the list to show only
fully-provisioned RECs. Doing that means the Keycloak Admin seam and reopening
celine-policies' ADR-0003. Decide that there, not here.
