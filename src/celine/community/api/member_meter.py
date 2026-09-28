"""A manager attaches or detaches a member's meter (ADR-0003, ADR-0004).

    dashboard ─▶ these routes ─▶ REC registry

The registry is written directly, with this BFF's own token. A token asked for
`rec-registry.assets.write` is used for the write and for nothing else; the member
and their meters are read with the default `rec-registry.read` token, which is the
one the Digital Twin receives too.

**The registry decides what is valid; this BFF decides who may press.** One active
holder per sensor id across communities is the registry's rule, and its refusal
code reaches the dashboard unchanged (`sensor_held`, `asset_key_taken`). Whether
the caller may press on this REC is `members.meter` in `policies/community.rego`.
No cache and no retry: pressing again is the manager's decision.

**The sensor id is typed, never offered.** Nothing here reads meter data to
suggest one: the only meters read are those of the one member the dialog is open
for.

**A name meets a sensor id only in the dialog.** The id travels in request and
response bodies, never in this BFF's paths. No log line and no audit row holds
it, and the registry's refusal sentence, which may name the asset key and so the
id, is neither logged nor forwarded. `httpx` logs the registry URL, whose asset
key carries the id, so its records are redacted below.

**Attach is for active members, detach for every member** (ADR-0004).
An attach to a member whose registry status is not
`active` is refused `409 member_not_active` before anything is written; a
detach is not, so a manager can free a meter a suspended member still holds.

**Every press that reaches the registry writes one audit row** (`community.member.meter.attach` or
`.detach`, resource `registry_member`, the member key, `{code, status}`), refusals
included. The "Sent emails" view reads invitation and password-reset rows only,
so these never appear there.
"""

import logging
import re
from typing import Annotated, Any

import httpx
from celine.sdk.auth import JwtUser
from celine.sdk.rec_registry import RecRegistryApiError
from fastapi import APIRouter, HTTPException, Path, Response

from celine.community.api.deps import (
    DbDep,
    MembersMeterDep,
    RegistryAssetsWriterDep,
    RegistryDep,
)
from celine.community.api.registry_press import (
    Outcome,
    Refused,
    audit,
    is_active,
    log_upstream,
    read,
    text,
)
from celine.community.api.schemas import (
    MemberMeter,
    MemberMeters,
    MeterAttach,
    MeterAttached,
    MeterDetach,
    MeterType,
)
from celine.community.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/communities/{community_key}/members/{member_key}/meter", tags=["members"]
)

AUDIT_ACTIONS: dict[str, str] = {
    "attach": "community.member.meter.attach",
    "detach": "community.member.meter.detach",
}

MemberKey = Annotated[str, Path(min_length=1, max_length=100)]

#: The registry's own ceiling for one page; a member holds a handful of meters.
_PAGE = 500


# ---------------------------------------------------------------------------
# httpx logs every request URL at INFO. A meter's asset key is `meter-<sensor id>`
# and sits in the registry path, so without this the id would reach the log.
# ---------------------------------------------------------------------------

_ASSET_PATH = re.compile(r"(/assets/)[^\s\"'?#]+")


class _RedactAssetKeys(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        redacted = _ASSET_PATH.sub(r"\1<redacted>", message)
        if redacted != message:
            record.msg = redacted
            record.args = None
        return True


_REDACTOR = _RedactAssetKeys()
for _name in ("httpx", "httpcore"):
    if not any(isinstance(f, _RedactAssetKeys) for f in logging.getLogger(_name).filters):
        logging.getLogger(_name).addFilter(_REDACTOR)


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------

#: Registry codes passed through with the registry's status.
_PASSED_THROUGH: dict[str, int] = {
    "sensor_held": 409,
    "asset_key_taken": 409,
    # A trimmed id of at most 122 characters keeps `meter-<id>` within the key's 128,
    # so this is not expected; mapped so it would still reach the dashboard by code.
    "asset_key_too_long": 422,
    "member_not_found": 404,
    "community_not_found": 404,
}


def trimmed(sensor_id: str) -> str:
    return sensor_id.strip()


def default_meter_type(role: str | None) -> MeterType:
    return "bidirectional" if (role or "").strip().casefold() == "prosumer" else "consumption"


async def _member(registry, community_key: str, member_key: str) -> Any:
    return await read(
        registry.get_member(community_key, member_key),
        "member",
        community_key,
        member_key,
        missing="member_not_found",
    )


async def _member_role(registry, community_key: str, member_key: str) -> str | None:
    return text(getattr(await _member(registry, community_key, member_key), "role", None))


async def _held_meters(registry, community_key: str, member_key: str) -> list[tuple[str, Any]]:
    """`(asset key, meter)` for every meter *member_key* holds, and no one else's."""
    held: list[tuple[str, Any]] = []
    cursor: str | None = None
    while True:
        page = await read(
            registry.list_meters(community_key, owner=member_key, limit=_PAGE, cursor=cursor),
            "meters",
            community_key,
            member_key,
            missing="member_not_found",
        )
        for item in getattr(page, "items", None) or []:
            # The registry filters by owner; checked again so a filter it ever
            # ignored could not show one member another's meter.
            if getattr(item, "owner_key", None) == member_key:
                held.append((item.key, item))
        next_cursor = getattr(page, "next_cursor", None)
        if not isinstance(next_cursor, str) or not next_cursor:
            return held
        cursor = next_cursor


def _sensor(item: Any) -> str | None:
    value = getattr(item, "sensor_id", None)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _meter_type(item: Any) -> str | None:
    value = getattr(item, "meter_type", None)
    return value if isinstance(value, str) and value else None


def _write_refused(exc: Exception, what: str, community_key: str, member_key: str) -> Refused:
    """How a refused registry write reads to the manager."""
    if isinstance(exc, RecRegistryApiError):
        status = exc.status_code
        # `exc.code` and the status only: the sentence may carry the asset key.
        log_upstream(what, community_key, member_key, status, exc.code)
        if exc.code in _PASSED_THROUGH:
            return Refused(_PASSED_THROUGH[exc.code], exc.code, status)
        if exc.code == "asset_not_found":
            return Refused(404, "meter_not_found", status)
        if status in (401, 403):
            # A missing grant is a deployment fault. A 403 here would tell the
            # manager *they* were refused.
            return Refused(502, "registry_refused", status)
        if status == 422:
            return Refused(422, exc.code or "meter_rejected", status)
        return Refused(502, "registry_unavailable", status)
    if isinstance(exc, httpx.HTTPStatusError):
        # Only the token provider raises this: Keycloak refusing the write token,
        # most often `invalid_scope` on a realm where svc-community does not hold
        # the optional scope.
        logger.error(
            "Keycloak refused this BFF's registry write token community=%s member=%s "
            "status=%s (is %s an optional scope of the client on this realm?)",
            community_key,
            member_key,
            exc.response.status_code,
            settings.rec_registry_assets_write_scope,
        )
        return Refused(502, "registry_refused", None)
    logger.warning(
        "REC Registry %s unavailable community=%s member=%s error=%s",
        what,
        community_key,
        member_key,
        type(exc).__name__,
    )
    return Refused(502, "registry_unavailable", None)


async def _audit(
    db, community_key: str, member_key: str, actor: JwtUser, press: str, outcome: Outcome
) -> None:
    """One row per press. Member key, code and status: never the sensor id."""
    detail = {"code": outcome.code, "status": outcome.upstream}
    await audit(db, community_key, member_key, actor, AUDIT_ACTIONS[press], detail)


async def _finish(
    db, community_key: str, member_key: str, user: JwtUser, press: str, outcome: Outcome
) -> None:
    await _audit(db, community_key, member_key, user, press, outcome)
    logger.info(
        "Member meter community=%s member=%s press=%s code=%s status=%s actor=%s",
        community_key,
        member_key,
        press,
        outcome.code,
        outcome.status,
        user.sub,
    )
    if outcome.status >= 400:
        raise HTTPException(status_code=outcome.status, detail={"code": outcome.code})


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("", response_model=MemberMeters)
async def member_meters(
    community_key: str,
    member_key: MemberKey,
    user: MembersMeterDep,
    registry: RegistryDep,
) -> MemberMeters:
    """The meters of the one member the dialog is open for, with the type an attach defaults to.

    Nothing else: no other member's meter and no candidate list. `404
    member_not_found` when the member is not in this REC.
    """
    try:
        role = await _member_role(registry, community_key, member_key)
        held = await _held_meters(registry, community_key, member_key)
    except Refused as refused:
        raise HTTPException(status_code=refused.status, detail={"code": refused.code}) from None
    return MemberMeters(
        member_key=member_key,
        default_meter_type=default_meter_type(role),
        meters=[
            MemberMeter(sensor_id=sensor, meter_type=_meter_type(item))
            for _, item in held
            if (sensor := _sensor(item)) is not None
        ],
    )


@router.put(
    "",
    response_model=MeterAttached,
    status_code=201,
    responses={200: {"model": MeterAttached, "description": "Already attached; nothing written"}},
)
async def attach_meter(
    community_key: str,
    member_key: MemberKey,
    body: MeterAttach,
    user: MembersMeterDep,
    registry: RegistryDep,
    writer: RegistryAssetsWriterDep,
    db: DbDep,
    response: Response,
) -> MeterAttached:
    """Attach the meter with this sensor id to the member.

    `201 attached`, written at `meter-<trimmed id>`. `200 already_attached` when
    the member already holds a meter with that id: nothing is written, and the
    answer carries the type it has. Changing a held meter's type is a detach and an
    attach. Refusals are `{"detail": {"code"}}`: `409 sensor_held` (another active
    member, in any REC, holds it; no member or REC is named), `409
    asset_key_taken` (another member holds the key, or this member holds
    `meter-<id>` for a different sensor id, which is not replaced), `404
    member_not_found`, `409 member_not_active` (the member's status is not
    `active`; checked after the member is read, before anything is written),
    `422 sensor_id_blank`, `422 asset_key_too_long` (not
    expected: the id is capped at 122 characters), `502 registry_unavailable`,
    `502 registry_refused`.
    """
    sensor_id = trimmed(body.sensor_id)
    if not sensor_id:
        # Refused before the registry is asked anything, so there is no press to audit.
        raise HTTPException(status_code=422, detail={"code": "sensor_id_blank"})

    meter_type: str = body.meter_type or "consumption"
    try:
        found = await _member(registry, community_key, member_key)
        if not is_active(found):
            # ADR-0004: attach is for active members only; nothing is read or written further.
            raise Refused(409, "member_not_active", 200)
        role = text(getattr(found, "role", None))
        held = await _held_meters(registry, community_key, member_key)
        existing = next((item for _, item in held if _sensor(item) == sensor_id), None)
        if existing is not None:
            meter_type = _meter_type(existing) or body.meter_type or default_meter_type(role)
            outcome = Outcome(200, "already_attached", 200)
        else:
            meter_type = body.meter_type or default_meter_type(role)
            asset_key = f"meter-{sensor_id}"
            if any(key == asset_key for key, _ in held):
                # The member already holds this key for another sensor id (imported
                # data that breaks the convention). The PUT would silently replace that
                # meter, so the press is refused and nothing is written.
                raise Refused(409, "asset_key_taken", 200)
            payload = {
                "key": asset_key,
                "asset_type": "meter",
                # The asset's name is not the member's: no personal data here.
                "properties": {"name": "Meter", "sensor_id": sensor_id, "meter_type": meter_type},
            }
            try:
                await writer.put_asset(community_key, member_key, asset_key, payload)
            except Exception as exc:  # noqa: BLE001
                raise _write_refused(exc, "meter attach", community_key, member_key) from None
            outcome = Outcome(201, "attached", 200)
    except Refused as refused:
        outcome = Outcome(refused.status, refused.code, refused.upstream)

    await _finish(db, community_key, member_key, user, "attach", outcome)
    response.status_code = outcome.status
    return MeterAttached(outcome=outcome.code, sensor_id=sensor_id, meter_type=meter_type)


@router.delete("", status_code=204, response_class=Response)
async def detach_meter(
    community_key: str,
    member_key: MemberKey,
    body: MeterDetach,
    user: MembersMeterDep,
    registry: RegistryDep,
    writer: RegistryAssetsWriterDep,
    db: DbDep,
) -> Response:
    """Detach the member's meter with this sensor id: a hard delete of that one asset.

    Open for every member whatever their status (ADR-0004), so a manager can free a
    meter held by a suspended or inactive member.
    The member's other meters are untouched. The sensor id is in the body, so it
    never reaches an access log. `204` when detached. `404 meter_not_found` when
    the member holds no meter with that id, `404 member_not_found`, `422
    sensor_id_blank`, `502 registry_unavailable`, `502 registry_refused`.
    """
    sensor_id = trimmed(body.sensor_id)
    if not sensor_id:
        raise HTTPException(status_code=422, detail={"code": "sensor_id_blank"})

    try:
        await _member_role(registry, community_key, member_key)
        held = await _held_meters(registry, community_key, member_key)
        asset_key = next((key for key, item in held if _sensor(item) == sensor_id), None)
        if asset_key is None:
            raise Refused(404, "meter_not_found", 200)
        try:
            await writer.delete_asset(community_key, member_key, asset_key)
        except Exception as exc:  # noqa: BLE001
            raise _write_refused(exc, "meter detach", community_key, member_key) from None
        outcome = Outcome(204, "detached", 204)
    except Refused as refused:
        outcome = Outcome(refused.status, refused.code, refused.upstream)

    await _finish(db, community_key, member_key, user, "detach", outcome)
    return Response(status_code=204)
