# METADATA
# title: Community Manager API Access Policy
# description: Single-REC access for the Community Manager Dashboard
# scope: package
# entrypoint: true
package celine.community.access

import rego.v1

default allow := false
default reason := "access denied"

is_service if input.subject.type == "service"

owns_community if {
    input.subject.claims.community_key != null
    input.subject.claims.community_key == input.resource.attributes.community_key
}

has_scope(scope) if scope in input.subject.scopes

has_manager_role if {
    some group in input.subject.groups
    group in {"manager", "managers", "rec-manager", "rec-managers", "admin", "admins"}
}

allow if {
    not is_service
    input.action.name in {"console.read", "community.read"}
    owns_community
    has_manager_role
}

allow if {
    not is_service
    input.action.name == "objectives.write"
    owns_community
    has_manager_role
}

allow if {
    not is_service
    input.action.name == "devices.read"
    owns_community
    has_manager_role
    has_scope("community.devices.read")
}

allow if {
    not is_service
    input.action.name == "flexibility.read"
    owns_community
    has_manager_role
    has_scope("community.read")
}

allow if {
    not is_service
    input.action.name == "gamification.read"
    owns_community
    has_manager_role
    has_scope("community.devices.read")
}

allow if {
    not is_service
    input.action.name == "nudging.read"
    owns_community
    has_manager_role
    has_scope("community.nudging.read")
}

allow if {
    not is_service
    input.action.name == "alerts.read"
    owns_community
    has_manager_role
    has_scope("community.read")
}

allow if {
    not is_service
    input.action.name == "alerts.write"
    owns_community
    has_manager_role
    has_scope("community.alerts.write")
}

allow if {
    is_service
    input.action.name in {"console.read", "community.read"}
    has_scope("community.read")
}

allow if {
    is_service
    input.action.name == "objectives.write"
    has_scope("community.objectives.write")
}

allow if {
    is_service
    input.action.name == "devices.read"
    has_scope("community.devices.read")
}

allow if {
    is_service
    input.action.name == "flexibility.read"
    has_scope("community.read")
}

allow if {
    is_service
    input.action.name == "gamification.read"
    has_scope("community.devices.read")
}

allow if {
    is_service
    input.action.name == "nudging.read"
    has_scope("community.nudging.read")
}

allow if {
    is_service
    input.action.name == "alerts.read"
    has_scope("community.read")
}

allow if {
    is_service
    input.action.name == "alerts.write"
    has_scope("community.alerts.write")
}

allow if has_scope("community.admin")

reason := "manager accessing assigned REC" if {
    allow
    not is_service
}

reason := "service scope granted" if {
    allow
    is_service
}

reason := "requested REC does not match the caller organization" if {
    not allow
    not is_service
    not owns_community
}

reason := "REC Manager role required" if {
    not allow
    not is_service
    owns_community
    not has_manager_role
}

reason := "community.devices.read scope required" if {
    not allow
    not is_service
    input.action.name == "devices.read"
    owns_community
    has_manager_role
    not has_scope("community.devices.read")
}

reason := "community.nudging.read scope required" if {
    not allow
    not is_service
    input.action.name == "nudging.read"
    owns_community
    has_manager_role
    not has_scope("community.nudging.read")
}

reason := "community.alerts.write scope required" if {
    not allow
    not is_service
    input.action.name == "alerts.write"
    owns_community
    has_manager_role
    not has_scope("community.alerts.write")
}

reason := "service scope missing" if {
    not allow
    is_service
}
