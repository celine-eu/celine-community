# ADR-0004 — A member's name meets a sensor id only in the meter dialog, and the id is typed, never offered

**Date:** 2026-09-27
**Status:** accepted

Extends [ADR-0002](ADR-0002-members-by-name-from-the-registry-and-sends-through-onboarding.md):
the members list gains a meter flag, and the person-only set gains two actions. Everything else
in ADR-0002 stands. The writes themselves are
[ADR-0003](ADR-0003-the-bff-writes-meter-role-and-area-to-the-registry-directly.md).

The members list's meter flag, the meter routes, `members.meter` and the dashboard's meter dialog
(celine-frontend `apps/community`) are implemented; `members.edit` is not yet. [`../acceptance.md`](../acceptance.md) lists what
is pending.

## Context

Until now two surfaces here were kept apart on purpose. The members page shows names and never a
device. Every analytical surface — devices, data flow, points, flexibility, exports — carries
`device_id` and never a name. Attaching a meter to a member cannot keep them apart: the manager
has to see whose meter they are entering.

A candidate list was considered, to spare the manager typing the id: the meters that report,
from the Digital Twin's meter fetchers, minus those already held. It cannot be scoped to a REC.
The meter data carries no community, the fetchers are not community-scoped, and this BFF's
service token is not row-filtered. An unattached meter belongs to no community yet, so on a
deployment with several RECs any candidate list would show one REC's manager the meters of
another REC's members, or invite them to attach one. Several RECs on one deployment are
supported, so the list was rejected (requester, 2026-09-27).

## Decision

**The sensor id is typed as free text.** No picker, no suggestion list, and no lookup of
unattached meters. The BFF reads no meter data to build the dialog. The id is trimmed, and the
registry's answer is the only feedback: attached; already attached to this member, which changes
nothing; or `409 sensor_held` when another active member, in any community, holds it. A
`sensor_held` answer names no member and no community outside the one on screen. A meter that
has not reported yet is accepted like any other.

**A name meets a sensor id only in the meter dialog.**
- The members list says whether a member has a meter, yes or no, and never shows the id.
- The dialog shows the sensor id of the member it is open for, and nothing else.
- No audit row, log line or browser storage holds a sensor id. The audit row names the member key
  and the outcome.
- Exports are unchanged: no meter flag and no sensor id.

**`members.meter` (attach, detach) and `members.edit` (role, area) are person-only actions.**
They are granted to the same groups as `members.read`: the REC organization's `admins` and
`managers`, and the realm `/admins` on every REC, as for `members.read` (requester, 2026-09-27).
Neither has a service scope, and the `community.admin` override does not reach them, so a service
token never attaches a meter or edits a profile. `GET /api/me` reports them only when the REC registry is
configured, so a dashboard without one offers no meter or edit action.

## Consequences

- **This service links a participant's name to a sensor id**, on a manager's screen, for the
  length of one dialog, and reads which members hold a meter. The personal-data inventory must say
  so. Nothing is stored.
- **A typo can attach the wrong meter** when the mistyped id is held by nobody. The registry cannot
  tell, and the dialog offers no "readings seen" hint. The manager detaches and attaches again;
  the next pipeline run follows.
- **What will tempt someone to undo this:** a picker "to avoid typos". It needs a source that says
  which REC an unattached meter belongs to — an ingest gateway, say — and none exists. Decide it
  with the requester, not here.
