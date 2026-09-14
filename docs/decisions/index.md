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
