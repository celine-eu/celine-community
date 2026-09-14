# METADATA
# title: Community Manager API Access Policy
# description: JWT-scoped REC access for the Community Manager Dashboard
# scope: package
# entrypoint: true
package celine.community.access

import rego.v1

# =============================================================================
# REC MANAGER DASHBOARD AUTHORIZATION
# =============================================================================
#
# Two subject types, authorised on different evidence — the split
# `celine.onboarding.access` and `celine.grid.access` both make:
#
#   * Managers (humans) are authorised by **group membership**. Keycloak has
#     already verified which organization they belong to, so the organization is
#     the tenancy boundary and the group is the role. They additionally carry the
#     per-surface `community.*` scopes oauth2-proxy mints on their token.
#   * Service accounts have no organization membership, so a **scope** is the
#     only way for them to express intent.
#
# Groups exist at two levels and the difference is load-bearing:
#
#   * A **realm**-level group (`groups` claim) is a platform-wide grant — every
#     REC on the deployment.
#   * An **organization**-level group (`organization.<alias>.groups`) grants the
#     capability for that REC only.
#
# `security/policy.py` passes these separately and never merges them. Merging is
# what `celine.sdk.auth.jwt.extract_groups` does, and it is the wrong thing here:
# a `managers` badge held inside REC A would otherwise satisfy a realm-level
# check and authorise an action on REC B. Until this rewrite that is exactly what
# `has_manager_role` did, and only a single-REC equality check stood in the way.
#
# An action name that appears in no table below is denied, so adding an endpoint
# without adding its capability fails closed.
#
# =============================================================================

default allow := false

default reason := "access denied"

# ── capability tables ────────────────────────────────────────────────────────

# `admins` and `managers`, and no one else. The realm also has `editors`,
# `viewers` and `participants`; onboarding grants its read capabilities to all
# four, and this dashboard deliberately does not. Every surface here is a manager
# surface. A read-only REC member is one more row in this table when someone
# needs one — not a redesign.
#
# The names are plural because the realm's groups are: /admins, /editors,
# /managers, /participants, /viewers. The `rec-manager`, `rec-managers`,
# `manager` and `admin` spellings this table used to accept matched no group that
# has ever existed in the realm.
required_groups := {
	"console.read": {"admins", "managers"},
	"community.read": {"admins", "managers"},
	"objectives.write": {"admins", "managers"},
	"devices.read": {"admins", "managers"},
	"flexibility.read": {"admins", "managers"},
	"gamification.read": {"admins", "managers"},
	"nudging.read": {"admins", "managers"},
	"alerts.read": {"admins", "managers"},
	"alerts.write": {"admins", "managers"},
	"members.read": {"admins", "managers"},
	"members.invite": {"admins", "managers"},
}

# A scope a human's token must carry *in addition* to the group. Orthogonal to
# the group check and unchanged by this rewrite: the group says which REC, the
# scope says which surface oauth2-proxy was willing to mint. An action absent
# here requires no scope.
required_scopes := {
	"devices.read": "community.devices.read",
	"gamification.read": "community.devices.read",
	"flexibility.read": "community.read",
	"alerts.read": "community.read",
	"nudging.read": "community.nudging.read",
	"alerts.write": "community.alerts.write",
}

# What a service account must hold instead. A service has no organization, so the
# scope is the whole grant and it reaches every REC by design — these callers are
# the pipelines and the exporters.
service_scopes := {
	"console.read": "community.read",
	"community.read": "community.read",
	"objectives.write": "community.objectives.write",
	"devices.read": "community.devices.read",
	"flexibility.read": "community.read",
	"gamification.read": "community.devices.read",
	"nudging.read": "community.nudging.read",
	"alerts.read": "community.read",
	"alerts.write": "community.alerts.write",
}

# Actions only a person may perform, whatever scope a service holds. They have no
# `service_scopes` entry, and the `community.admin` superset below skips them.
# `members.read` puts participant names on a screen, and `members.invite` sends
# an email, which only ever follows a person's decision. A service that could do
# either through this BFF would be a way round both.
person_only_actions := {"members.read", "members.invite"}

known_action if required_groups[input.action.name]

# ── subject helpers ──────────────────────────────────────────────────────────

is_service if input.subject.type == "service"

has_scope(scope) if scope in input.subject.scopes

has_required_scope if not required_scopes[input.action.name]

has_required_scope if has_scope(required_scopes[input.action.name])

# A realm-level group grants the action on every REC, so no organization check.
# This is what "an admin or manager sees every REC" means, and it is the branch
# that cannot come from the organization claim: the token names only the
# organizations the caller belongs to, never the deployment's full REC list.
granted_by_realm_group if {
	some g in required_groups[input.action.name]
	g in input.subject.groups
}

# An organization-level group grants the action for that organization's REC only.
# `claims.organization` is the caller's organization as resolved *against this
# request's target* — null when they are not a member of the REC being asked
# about — and `claims.org_groups` holds that one organization's groups.
#
# `org_type == "rec"` is the requirement's own filter and not decoration: a
# Keycloak organization is also how a DSO and an ordinary company are modelled,
# and `managers` inside either of those must not reach a REC dashboard.
granted_by_org_group if {
	input.subject.claims.organization != null
	input.subject.claims.organization == input.resource.attributes.community_key
	input.subject.claims.org_type == "rec"
	some g in required_groups[input.action.name]
	g in input.subject.claims.org_groups
}

# ── rules ────────────────────────────────────────────────────────────────────

allow if {
	not is_service
	has_required_scope
	granted_by_realm_group
}

allow if {
	not is_service
	has_required_scope
	granted_by_org_group
}

allow if {
	is_service
	has_scope(service_scopes[input.action.name])
}

# The superset, and service-only. It used to be a bare rule granting anyone who
# held the scope; `community.admin` is a service-account scope that no human
# token carries, so the bare form was an organization-check-free bypass waiting
# for someone to mint it onto a user.
allow if {
	is_service
	has_scope("community.admin")
	not input.action.name in person_only_actions
}

# ── reasons ──────────────────────────────────────────────────────────────────
#
# One else-chain rather than independent rules: two `reason` rules matching the
# same request is a rego conflict error, not a precedence question.

reason := "granted by realm group" if {
	not is_service
	allow
	granted_by_realm_group
} else := "granted by organization group" if {
	not is_service
	allow
} else := "granted by service scope" if {
	is_service
	allow
} else := "unknown action — no capability is declared for it" if {
	not known_action
} else := "only a person may perform this action, never a service" if {
	is_service
	input.action.name in person_only_actions
} else := "service is missing a scope granting this action" if {
	is_service
} else := sprintf("%s scope required", [required_scopes[input.action.name]]) if {
	not has_required_scope
} else := "caller is not a member of this REC" if {
	input.subject.claims.organization != input.resource.attributes.community_key
} else := "the caller's organization is not a REC" if {
	input.subject.claims.org_type != "rec"
} else := "REC admins or managers group required"
