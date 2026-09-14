# ADR-0002 — Members are listed by name from the REC registry and never stored, and sends reach the provisioning service only through onboarding

**Date:** 2026-09-14
**Status:** accepted

## Context

A manager needs to re-send an invitation to a member, or send them a password reset. An
invitation link lasts 7 days. Members imported from a registry bundle were never invited
at all. Onboarding's approval records "a manager can re-send from `celine-community`", and
until now nothing here could.

That forced two choices.

**How does a manager find the member?** Until now this BFF said "participant identity is
never resolved or persisted", and returned aggregates and `device_id` values only. A list
of member keys is useless to a person. The requester decided that names are needed
(2026-09-14, A1). A name lives in three places:
- **the REC registry.** `Member.name` is `NOT NULL`, and this service already holds
  `rec-registry.read`.
- **Keycloak** (`firstName`, `lastName`). Only the provisioning service reads the realm,
  and it has no read route.
- **onboarding submissions.** They cover only people who used the wizard, and they carry
  fiscal and supply data beside the name.

**What does the BFF call to send?** The provisioning service asks Keycloak to send the
email. The first design gave `svc-community` a `provisioning.*` scope and called it
directly. The requester reversed that (2026-09-14, A3): `provisioning.*` is reserved for
onboarding, so the fewer holders the better.

## Decision

**Read names from the REC registry, per request, and keep none.**
- `GET /api/communities/{community_key}/members` returns one registry page of `key`,
  `name`, `role`, `status` and `area`. It never returns `user_id`, `did`, delivery points
  or an address.
- No database row, cache or log line holds a name. An audit row names the member key.
- A name that only repeats the key is returned as `null`.

**Make members person-only actions.** `members.read` and `members.invite` are granted to
`admins` and `managers` like every action, and to no service. `community.admin` does not
reach them.

**Send through onboarding only.** The BFF calls onboarding's member-keyed admin routes, and
onboarding calls the provisioning service. `svc-community` holds no `provisioning.*` scope
and makes no Keycloak call ([ADR-0001](ADR-0001-the-rec-registry-is-the-rec-universe.md)
stands).

## Consequences

- **This service is a recipient of participant names.** The personal-data inventory must
  say so. The names reach the members page only, never exports, the overview, the device
  board or the alerts feed.
- **Search is only as good as one registry page.** The registry's `list_members` has no
  text filter, so `q` narrows each page inside the BFF, and the dashboard says so. A
  community large enough for that to hurt needs a registry-side filter, not a cache here.
  A cache would be a stored copy of names.
- **A placeholder name is shown as it is.** `Participant GL-00001` from an import is not
  the key, and nothing here can tell it is not a name.
- **Onboarding becomes a runtime dependency** of the send buttons, and one more hop that can
  fail. Without `ONBOARDING_URL`, `GET /api/me` does not report `members.invite`, so the
  buttons are not offered.
- **What will tempt someone to undo this:**
  - a Keycloak name "because it is more current". That needs a provisioning read route
    reachable from this BFF, which A3 rules out.
  - granting `provisioning.participants.write` to `svc-community` "to save a hop". That
    reopens A3. Decide it with the requester, not here.
