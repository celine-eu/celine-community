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
#   * Managers (humans) are authorised by **organization group membership**, or
#     by the `platform-admin` realm role. Keycloak has already verified which
#     organization they belong to, so the organization is the tenancy boundary
#     and the group is the role. They additionally carry the
#     per-surface `community.*` scopes oauth2-proxy mints on their token.
#   * Service accounts have no organization membership, so a **scope** is the
#     only way for them to express intent.
#
# A human's grant exists at exactly two levels and the difference is load-bearing:
#
#   * The **platform** level is the Keycloak realm *role* `platform-admin`
#     (`realm_access.roles`, passed as `input.subject.roles`). It is the only
#     platform-wide grant — every REC on the deployment.
#   * An **organization**-level group (`organization.<alias>.groups`) grants the
#     capability for that REC only.
#
# Realm *groups* (`/admins`, `/managers`, ...) are no longer a grant at any level:
# their names collide with the organization groups', so the same `admins` meant
# two things. `security/policy.py` passes `input.subject.groups` empty, and this
# policy never reads it — a realm group still present in a token grants nothing.
#
# `security/policy.py` also passes only the organization matching the request,
# never a merged list: a `managers` badge held inside REC A must not authorise an
# action on REC B.
#
# An action name that appears in no table below is denied, so adding an endpoint
# without adding its capability fails closed.
#
# =============================================================================

default allow := false

default reason := "access denied"

# ── capability tables ────────────────────────────────────────────────────────

# Organization `admins` and `managers`, and no one else. Organizations also have
# `editors` and `viewers`; onboarding grants its read capabilities to them, and this
# dashboard deliberately does not. Every surface here is a manager surface. A
# read-only REC member is one more row in this table when someone needs one — not
# a redesign.
#
# The names are plural because the organization groups are: /admins, /managers,
# /editors, /viewers. The `rec-manager`, `rec-managers`, `manager` and `admin`
# spellings this table used to accept matched no group that has ever existed.
required_org_groups := {
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
	"members.meter": {"admins", "managers"},
	"members.edit": {"admins", "managers"},
	# Releasing a member ends their membership: data sharing withdrawn, dataspace
	# credential revoked, login removed from the REC, registry member inactive.
	# The REC's administrators decide that, not its managers (requester,
	# 2026-10-05), which is also what onboarding re-checks from the forwarded token.
	"members.release": {"admins"},
}

# The platform-wide grant: a Keycloak realm role, never a group. It reaches every
# action in `required_org_groups` on every REC. No other realm role grants
# anything — `default-roles-celine`, `offline_access`, and the retired `admin`,
# `manager`, `editor` and `viewer` roles included.
platform_admin_role := "platform-admin"

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
# an email, which only ever follows a person's decision. `members.meter` attaches
# or detaches a member's meter, which puts a name beside a sensor id in one dialog
# and changes whose readings the meter's rows are (ADR-0004). `members.edit`
# corrects a member's role or area, which changes how their meter's rows are
# settled from the next pipeline run (ADR-0003). `members.release` ends a person's
# membership of the REC. A service that could do any of them through this BFF
# would be a way round all five.
person_only_actions := {"members.read", "members.invite", "members.meter", "members.edit", "members.release"}

known_action if required_org_groups[input.action.name]

# ── subject helpers ──────────────────────────────────────────────────────────

is_service if input.subject.type == "service"

has_scope(scope) if scope in input.subject.scopes

has_required_scope if not required_scopes[input.action.name]

has_required_scope if has_scope(required_scopes[input.action.name])

# The `platform-admin` realm role grants every known action on every REC, so no
# organization check. Organization managers deliberately have no platform-wide
# branch, and `input.subject.groups` is read by no rule.
granted_by_platform_role if {
	known_action
	platform_admin_role in object.get(input.subject, "roles", [])
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
	some g in required_org_groups[input.action.name]
	g in input.subject.claims.org_groups
}

# ── rules ────────────────────────────────────────────────────────────────────

allow if {
	not is_service
	has_required_scope
	granted_by_platform_role
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

reason := "granted by platform role" if {
	not is_service
	allow
	granted_by_platform_role
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
} else := "REC admins group required" if {
	not "managers" in required_org_groups[input.action.name]
} else := "REC admins or managers group required"
