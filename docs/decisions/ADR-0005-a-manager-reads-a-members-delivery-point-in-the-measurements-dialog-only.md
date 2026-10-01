# ADR-0005 — A manager reads a member's delivery point in the measurements dialog only, and never writes it

**Date:** 2026-10-01
**Status:** accepted

Amends [ADR-0002](ADR-0002-members-by-name-from-the-registry-and-sends-through-onboarding.md) and
[ADR-0004](ADR-0004-a-name-meets-a-sensor-id-only-in-the-meter-dialog.md) on one point: both say
no delivery point leaves this BFF. That stays true for writing and for every surface but one.
Everything else in both stands, and so does
[ADR-0003](ADR-0003-the-bff-writes-meter-role-and-area-to-the-registry-directly.md).

## Context

A member has two measurement sources, and the dashboard used to name only one of them, with the
other's word:

- the **delivery point (POD)**: the DSO's grid connection, with the DSO's official meter. Nearly
  every member has one; onboarding records it from what the member declared, and it may be missing
  when the member did not know it. The registry holds it in `Member.delivery_points`.
- the **meter**: a REC- or member-provided device (IoT, say) beside the POD. Optional. The registry
  holds it as a `meter` asset with a `sensor_id`.

The meter dialog showed only the meter, and called it by the word members use for the POD
("contatore"). A manager attaching a meter could not see whether the member had a POD at all, or
link the meter to it. The requester (2026-10-01) asked to review both in one dialog, called
"measurements", and asked whether the manager should also edit the POD, since members often do not
know it.

**Why the POD is not written here.** A POD written outside onboarding would bypass the consent
relay: the DSO's connector filters on the POD values copied into the consent row at grant time,
onboarding refreshes them only on a grant, and the consent text names the POD the member provided
when they joined. Onboarding also holds the links to the dataspace and to provisioning, and an
operator validates the member's input there anyway. So a correction is an onboarding operator's,
through onboarding's tracked revisions (requester, 2026-10-01).

## Decision

**The measurements dialog shows the member's delivery points, read-only.** `GET …/meter` (the route
keeps its name) adds `deliveryPoints: [{id, active}]`, read from the registry member detail the BFF
already fetched, and `meters[].pod`, the delivery point a meter is linked to (`properties.pod`), or
`null`. The id is returned as the registry spells it. Nothing else of a delivery point leaves the BFF:
no address, tariff or description. The dialog is open for every member, whatever their status.

**The members list says whether a member has a delivery point, yes or no.** `hasDeliveryPoint` is
the registry's delivery point count, greater than zero; never which POD.

**An attach may link the meter to one of the member's delivery points, optionally.** `PUT …/meter`
takes an optional `pod`. It must be one the member holds, compared trimmed and case-insensitively,
else `422 pod_not_held` and nothing is written. What is written to `properties.pod` is the registry's
own spelling of that id, never the caller's, so the registry's relink and onboarding's POD-list
export compare like with like. Without `pod`, none is written. An attach that finds the meter already
held changes nothing, the link included; changing a link is a detach and an attach.

**A delivery point is never written by this BFF.** No route edits, adds or removes one, and the
dialog's empty POD section says the POD is set through onboarding. No registry scope is added.

**No POD in an audit row or a log line**, as no sensor id (ADR-0004). The audit row of an attach is
still the member key, the code and the status.

## Consequences

- **A manager now reads a member's POD**, on screen, for the length of one dialog. The POD is
  personal data (it locates the member's premises); the personal-data inventory says so. Nothing is
  stored, cached or exported, and the members list carries a yes/no flag only.
- **The link is advisory.** Settlement is meter-based and the registry mirror takes PODs from the
  member, not the meter, so a link changes no settlement data. Onboarding's POD-list export unions
  meter `properties.pod`; `pod_not_held` keeps a link from adding a POD the member does not hold.
- **An inactive delivery point is still the member's.** It is shown, marked inactive, and an attach
  may link to it; the registry decides what an inactive POD means downstream.
- **What will tempt someone to undo this:** a "fix the POD here" button, because managers know the
  POD members forgot. It bypasses the consent relay and onboarding's validation. The correction
  belongs to onboarding's operator flow; decide any change with the requester, not here.
