# ADR-0003 — The BFF writes a member's meter, role and area to the REC registry directly, with two optional scopes

**Date:** 2026-09-27
**Status:** accepted

Extends [ADR-0001](ADR-0001-the-rec-registry-is-the-rec-universe.md): the registry client is no
longer read-only. Its Keycloak decision stands, and so does
[ADR-0002](ADR-0002-members-by-name-from-the-registry-and-sends-through-onboarding.md)'s rule that
sends reach the provisioning service only through onboarding.

The meter attach and detach are implemented; the profile write is not yet.
[`../acceptance.md`](../acceptance.md) lists what is pending.

## Context

A member approved through onboarding has no meter. Onboarding registers none by design: a
`sensor_id` exists only once the device is physically installed, and registering it is the REC
manager's work. Onboarding also sets the member's `role` from the self-declared "has PV" answer
and their `area` from the supply municipality, and nothing lets a manager correct either. Both
matter downstream: the area decides which primary substation the meter's rows are attributed to,
and the role decides whether its production counts.

Meter, role and area are **REC registry data**. Until now this BFF read the registry with
`rec-registry.read` only, and ADR-0001 says the registry "answers enumeration and naming only".
Two routes to the registry were possible.

**Through onboarding**, as the sends are. ADR-0002 routes sends through onboarding because
`provisioning.*` is reserved for onboarding, so the fewer holders the better. That reservation is
about the provisioning service; the requester confirmed on 2026-09-14 that it does not constrain
registry writes. Routing registry writes through onboarding would add a hop and turn onboarding
into a general member-administration service for data it does not own.

**Directly**, with this BFF's own token. The registry already enforces what a valid member is
and authorises by scope only, so the BFF needs a scope, not a new service.

## Decision

**Write meter, role and area to the REC registry directly, one registry call per press.**

- **Attach a meter:** `PUT /admin/communities/{community_key}/members/{member_key}/assets/meter-<sensor_id>`,
  with the trimmed sensor id, `asset_type: meter`, and a `meter_type` that defaults from the role
  (`prosumer` → `bidirectional`, otherwise `consumption`) and that the manager may change. The id is
  at most 122 characters, so the key fits the registry's 128. The registry's `PUT` is an upsert, so
  when the member already holds `meter-<sensor_id>` for a different sensor id the BFF refuses
  `409 asset_key_taken` instead of replacing it.
- **Detach a meter:** `DELETE` of that asset. It is a hard delete: the registry keeps no dated
  holding.
- **Edit role and area:** `PATCH /admin/communities/{community_key}/members/{member_key}/profile`
  with `{role?, area?}` and nothing else. The registry's dedicated profile route, not its general
  member `PATCH`.

**The dashboard moves a role between `consumer` and `prosumer` only.** Settlement counts a meter's
production only for a `prosumer` (`celine-pipelines`, `rec_virtual_consumption_15m.sql`), so these
two are the choice that changes what a member is credited with. A `producer`, like an imported
`operator` or `admin`, is shown read-only: the BFF refuses a profile call that sets any other role,
or that changes the role of a member whose current role is neither of the two, before it reaches
the registry. The area of every member stays editable.

**Request a write scope only for the write.** Reads keep `rec-registry.read`. The attach and
detach calls use a token requested with `rec-registry.assets.write`, the profile call one with
`rec-registry.members.profile.write`. Both are optional scopes of `svc-community`, never
default ones, because the Digital Twin forwards the BFF's default token. `svc-community` never
holds `rec-registry.members.write` or `rec-registry.admin`: either would let the dashboard rewrite
a member's `user_id`, `did` and status.

**The registry decides what is valid, this BFF decides who may press.** The registry enforces one
active holder per sensor id across communities (`sensor_held`), the role and status enums and the
area keys, and its refusal codes reach the dashboard unchanged. Whether a caller may press, and on
which REC, is decided by `policies/community.rego` from the token
([ADR-0004](ADR-0004-a-name-meets-a-sensor-id-only-in-the-meter-dialog.md) names the
capabilities). There is no cache and no retry: pressing again is the manager's decision.

**Every press that reaches the registry writes one `audit_events` row**, naming the member key and
the outcome and carrying no sensor id. The "Sent emails" view does not list these rows: it reads
only invitation and password-reset rows.

**Areas are read here, never written.** The dialog's area select and the read-only area map read
the community's areas from the registry and their boundary shapes from the Digital Twin. Areas and
topology are declared in onboarding templates and synced to the registry by onboarding; this BFF
writes neither.

**Nothing here knows about imported placeholders.** An attach, a detach and a profile edit mean the
same thing for every member. A sensor still held by a placeholder, in any community, answers
`sensor_held` like any other holder.

## Consequences

- **The registry grants are registry-wide**, as every registry grant is: `svc-community` could
  write any community's meters and profiles. `policies/community.rego` is what keeps a manager to
  their REC, and this service's audit row is the only record of who pressed, because the registry
  keeps none. The same trade was accepted for reading names.
- **The registry becomes a write dependency.** A registry outage refuses these presses and leaves
  every other surface as it was; ADR-0001's degraded REC list is unchanged.
- **A role or area change moves data from the next pipeline run:** the role decides whether the
  meter's production counts, the area decides its substation. A full refresh rewrites history with
  the new values. The dashboard says so before saving.
- **A detach loses the pointer from past readings to that member.** The readings stay in the data
  and follow the sensor's next holder. Dated holdings are the follow-up if that ever matters.
- **Provisioning is untouched.** `svc-community` still holds no `provisioning.*` scope and makes no
  Keycloak call.
- **What will tempt someone to undo this:**
  - granting `rec-registry.members.write` "to use one route for everything". That reopens a
    member's account id, DID and status to the dashboard. Decide it with the requester.
  - routing these writes through onboarding "so there is one gateway". That gateway exists for the
    provisioning service, not for registry data.
  - offering `producer` in the role select "because the registry accepts it". Settlement would
    stop counting that member's production as their own; decide it with the requester, together
    with the pipelines.
