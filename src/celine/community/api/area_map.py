"""The read-only area map: each area's primary-substation boundary, as a shape (ADR-0003).

    dashboard ─▶ this route ─▶ REC registry (the areas and their boundary ids)
                            └▶ Digital Twin `boundary_shape` (the shapes)

The areas, and the boundary each references, are read from the registry with the
default `rec-registry.read` token, as `GET …/areas` reads them. The shapes come
from the Digital Twin's `boundary_shape` value fetcher, called with this BFF's
default Digital Twin token; the Digital Twin queries dataset-api with its own
service identity, because the boundaries are open reference data. Nothing here
writes an area or a shape: areas are declared in onboarding templates.

A boundary shape is public reference data and names no member, so the answer is
cached briefly like the other REC aggregates. No shape and no coordinate is
logged: log lines name the REC, the fetcher and the error type.
"""

import json
import logging
from typing import Any

import httpx
from celine.sdk.dt import DTClient
from celine.sdk.dt.util import DTApiError
from celine.sdk.openapi.dt.errors import UnexpectedStatus
from fastapi import APIRouter, HTTPException

from celine.community.api import deps
from celine.community.api.deps import CommunityReadDep, RegistryDep
from celine.community.api.member_profile import community_areas
from celine.community.api.registry_press import Refused, read
from celine.community.api.schemas import AreaShape, AreaShapes, CommunityArea
from celine.community.services.cache import aggregate_cache
from celine.community.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/communities/{community_key}", tags=["members"])

FETCHER = "boundary_shape"

#: The most ids one `boundary_shape` request may name (the fetcher's own limit).
MAX_IDS_PER_CALL = 100

#: The `source` values the fetcher's payload schema accepts (its closed enum).
BOUNDARY_SOURCES = frozenset({"gse_cabine_primarie"})

#: The longest boundary id the fetcher's payload schema accepts.
MAX_BOUNDARY_ID_LENGTH = 64


def _askable(source: str, boundary_id: str) -> bool:
    """Whether the fetcher's payload schema accepts this boundary reference.

    The registry stores any `{source, id}`; the fetcher refuses a whole request
    with a 4xx when one id is too long or the source is outside its enum. Such an
    area is answered with no geometry rather than failing the whole map.
    """
    return source in BOUNDARY_SOURCES and 0 < len(boundary_id) <= MAX_BOUNDARY_ID_LENGTH


def _geometry(raw: Any) -> dict | None:
    """The fetcher's `geojson` string as a GeoJSON geometry object, or None."""
    if isinstance(raw, dict):
        value = raw
    elif isinstance(raw, str):
        try:
            value = json.loads(raw)
        except ValueError:
            return None
    else:
        return None
    return value if isinstance(value, dict) and isinstance(value.get("type"), str) else None


def _items(result: Any) -> list[dict[str, Any]]:
    raw = getattr(result, "items", None) or []
    return [item.to_dict() if hasattr(item, "to_dict") else dict(item) for item in raw]


async def _shapes(
    dt: DTClient, community_key: str, source: str, ids: list[str]
) -> dict[str, dict | None]:
    """`{boundary id: geometry}` for *ids* of one *source*; an id the DT lacks is absent."""
    found: dict[str, dict | None] = {}
    for start in range(0, len(ids), MAX_IDS_PER_CALL):
        chunk = ids[start : start + MAX_IDS_PER_CALL]
        try:
            result = await dt.communities.fetch_values(
                community_id=community_key,
                fetcher_id=FETCHER,
                payload={"source": source, "ids": chunk},
            )
        except (DTApiError, UnexpectedStatus) as exc:
            # The status only: the body could repeat the request.
            status = int(exc.status_code) if exc.status_code is not None else None
            logger.warning(
                "Digital Twin %s refused community=%s status=%s",
                FETCHER,
                community_key,
                status,
            )
            if status in (401, 403):
                # A missing grant is a deployment fault, not the manager's refusal.
                raise Refused(502, "digital_twin_refused", status) from None
            raise Refused(502, "digital_twin_unavailable", status) from None
        except httpx.HTTPStatusError as exc:
            # Only the token provider raises this: Keycloak refusing this BFF's
            # Digital Twin token.
            logger.error(
                "Keycloak refused this BFF's Digital Twin token community=%s status=%s",
                community_key,
                exc.response.status_code,
            )
            raise Refused(502, "digital_twin_refused", None) from None
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Digital Twin %s unavailable community=%s error=%s",
                FETCHER,
                community_key,
                type(exc).__name__,
            )
            raise Refused(502, "digital_twin_unavailable", None) from None
        for item in _items(result):
            boundary_id = item.get("id")
            if isinstance(boundary_id, str) and boundary_id in chunk:
                found[boundary_id] = _geometry(item.get("geojson"))
    return found


async def _area_shapes(registry, dt_client, community_key: str) -> AreaShapes:
    community = await read(
        registry.get_community(community_key),
        "community",
        community_key,
        "-",
        missing="community_not_found",
    )
    with_boundary: list[CommunityArea] = [
        area for area in community_areas(community) if area.boundary is not None
    ]
    if not with_boundary:
        return AreaShapes(community_key=community_key, areas=[])

    dt = dt_client()
    by_source: dict[str, list[str]] = {}
    for area in with_boundary:
        if area.boundary is None or not _askable(area.boundary.source, area.boundary.id):
            continue
        ids = by_source.setdefault(area.boundary.source, [])
        if area.boundary.id not in ids:
            ids.append(area.boundary.id)

    shapes: dict[tuple[str, str], dict | None] = {}
    for source, ids in by_source.items():
        for boundary_id, geometry in (await _shapes(dt, community_key, source, ids)).items():
            shapes[(source, boundary_id)] = geometry

    return AreaShapes(
        community_key=community_key,
        areas=[
            AreaShape(
                area_key=area.key,
                name=area.name,
                boundary_id=area.boundary.id,
                geometry=shapes.get((area.boundary.source, area.boundary.id)),
            )
            for area in with_boundary
            if area.boundary is not None
        ],
    )


def _dt_client() -> DTClient:
    if not settings.digital_twin_api_url:
        raise Refused(503, "digital_twin_not_configured", None)
    return deps.get_dt_client()


@router.get("/areas/shapes", response_model=AreaShapes)
async def area_shapes(
    community_key: str,
    user: CommunityReadDep,
    registry: RegistryDep,
) -> AreaShapes:
    """The shape of each of the REC's areas that references a boundary, for the map.

    `{communityKey, areas: [{areaKey, name, boundaryId, geometry}]}`, in area-key
    order. An area without a boundary is not listed. `geometry` is a GeoJSON
    geometry object simplified for display, or `null` when the Digital Twin has
    no shape for that boundary id or the boundary reference is one the Digital
    Twin's `boundary_shape` cannot be asked for (a source outside its enum, or an
    id longer than 64 characters); such a boundary is not sent to it. Cached for the aggregate cache's TTL.

    Refusals are `{"detail": {"code"}}`: `404 community_not_found`, `502
    registry_unavailable`, `502 registry_refused`, `502 digital_twin_unavailable`
    (unreachable, or any answer but a shape list), `502 digital_twin_refused`
    (the Digital Twin refused this BFF's token), `503
    digital_twin_not_configured`.
    """
    try:
        value, _ = await aggregate_cache.get_or_set(
            ("area_shapes", community_key),
            lambda: _area_shapes(registry, _dt_client, community_key),
        )
    except Refused as refused:
        raise HTTPException(status_code=refused.status, detail={"code": refused.code}) from None
    return value
