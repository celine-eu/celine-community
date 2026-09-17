"""Aggregate cache behavior."""

import asyncio

from celine.community.services.cache import AggregateCache


async def test_cache_reuses_value_for_the_same_rec_and_period() -> None:
    cache = AggregateCache(ttl_seconds=30)
    calls = 0

    async def produce() -> dict[str, int]:
        nonlocal calls
        calls += 1
        return {"calls": calls}

    first, first_hit = await cache.get_or_set(("overview", "example_rec", "7d"), produce)
    second, second_hit = await cache.get_or_set(
        ("overview", "example_rec", "7d"), produce
    )
    other_period, _ = await cache.get_or_set(("overview", "example_rec", "30d"), produce)
    # One process now serves several RECs. Two RECs asking for the same surface
    # over the same period must not be one entry.
    other_rec, other_rec_hit = await cache.get_or_set(("overview", "other_rec", "7d"), produce)

    assert first == second == {"calls": 1}
    assert first_hit is False
    assert second_hit is True
    assert other_period == {"calls": 2}
    assert other_rec == {"calls": 3}
    assert other_rec_hit is False


async def test_cache_prevents_a_same_key_stampede() -> None:
    cache = AggregateCache(ttl_seconds=30)
    calls = 0

    async def produce() -> int:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return calls

    values = await asyncio.gather(
        *(cache.get_or_set(("chain", "example_rec", "30d"), produce) for _ in range(5))
    )

    assert [value for value, _ in values] == [1, 1, 1, 1, 1]
    assert calls == 1
