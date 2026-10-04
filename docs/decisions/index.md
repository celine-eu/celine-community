# Decisions

Architecture decision records: **why a technical choice was made here**, when the reason
is not derivable from the code and would otherwise be re-litigated.

One file per decision, named `ADR-####-short-slug.md`, with this shape:

```markdown
# ADR-0001 — <the decision, as a statement>

**Date:** <ISO-8601>
**Status:** accepted | superseded by ADR-####

## Context
<what forced a choice. The constraint, and what had already been tried.>

## Decision
<what was decided, in the imperative.>

## Consequences
<what this costs, what it forecloses, and what will tempt someone to undo it.>
```

An ADR is immutable once accepted. It is superseded by a later ADR that names it, never
edited to say something else.

## The records

| ADR | Decision |
|---|---|
| [ADR-0001](ADR-0001-the-rec-registry-is-the-rec-universe.md) | The REC registry answers which RECs exist; the BFF has no Keycloak Admin interface |
| [ADR-0002](ADR-0002-members-by-name-from-the-registry-and-sends-through-onboarding.md) | Members are listed by name from the registry, never stored; sends reach the provisioning service only through onboarding |
| [ADR-0003](ADR-0003-the-bff-writes-meter-role-and-area-to-the-registry-directly.md) | The BFF writes a member's meter, role and area to the registry directly, with two optional scopes requested only for the write |
| [ADR-0004](ADR-0004-a-name-meets-a-sensor-id-only-in-the-meter-dialog.md) | A member's name meets a sensor id only in the meter dialog; the id is typed, never offered; `members.meter` and `members.edit` are person-only |
| [ADR-0005](ADR-0005-a-manager-reads-a-members-delivery-point-in-the-measurements-dialog-only.md) | A manager reads a member's delivery point (POD) in the measurements dialog only, read-only; an attach may link a meter to a POD the member holds (`pod_not_held`); amends ADR-0002 and ADR-0004 |
| [ADR-0006](ADR-0006-the-platform-level-is-a-realm-role-not-a-realm-group.md) | The only platform-wide grant is the realm role `platform-admin`; an organization's `admins`/`managers` grant that REC only, and a realm group grants nothing; narrows ADR-0001 and ADR-0004 |
