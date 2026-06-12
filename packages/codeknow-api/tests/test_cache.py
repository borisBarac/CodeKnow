"""Tests for codeknow_api.cache — Redis-based search cache."""

from __future__ import annotations

import json
from typing import Any

import fakeredis.aioredis
import pytest
from codeknow_api import cache
from codeknow_api.cache import RedisService


@pytest.fixture
async def fake_redis() -> fakeredis.aioredis.FakeRedis:
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
async def with_fake_service(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> fakeredis.aioredis.FakeRedis:
    service = RedisService(enabled=True, url="redis://fake")
    service._client = fake_redis
    cache.set_default_service(service)
    yield fake_redis
    cache.set_default_service(RedisService(enabled=False))


@pytest.fixture
async def with_disabled_service() -> None:
    cache.set_default_service(RedisService(enabled=False))
    yield
    cache.set_default_service(RedisService(enabled=False))


class TestMakeKey:
    def test_deterministic(self) -> None:
        a = cache._make_key("hello", ["a"], 10)
        b = cache._make_key("hello", ["a"], 10)
        assert a == b

    def test_different_inputs(self) -> None:
        k1 = cache._make_key("hello", ["a"], 10)
        k2 = cache._make_key("world", ["a"], 10)
        k3 = cache._make_key("hello", ["b"], 10)
        k4 = cache._make_key("hello", ["a"], 20)
        assert len({k1, k2, k3, k4}) == 4

    def test_repo_order_irrelevant(self) -> None:
        k1 = cache._make_key("q", ["a", "b"], 5)
        k2 = cache._make_key("q", ["b", "a"], 5)
        assert k1 == k2

    def test_none_repos(self) -> None:
        k1 = cache._make_key("q", None, 5)
        k2 = cache._make_key("q", ["a"], 5)
        assert k1 != k2


class TestBodyReferencesSlug:
    def test_top_level_slug(self) -> None:
        assert cache._body_references_slug({"slug": "owner/repo"}, "owner/repo")

    def test_repos_list(self) -> None:
        assert cache._body_references_slug({"repos": ["a", "owner/repo"]}, "owner/repo")

    def test_result_item_slug(self) -> None:
        assert cache._body_references_slug(
            {"results": [{"slug": "owner/repo", "score": 0.9}]},
            "owner/repo",
        )

    def test_no_match(self) -> None:
        assert not cache._body_references_slug(
            {"slug": "other/repo", "repos": ["x"]},
            "owner/repo",
        )

    def test_no_false_positive_substring(self) -> None:
        assert not cache._body_references_slug(
            {"slug": "owner/repo-extra"},
            "owner/repo",
        )

    def test_non_dict_data(self) -> None:
        assert not cache._body_references_slug("not a dict", "owner/repo")
        assert not cache._body_references_slug([], "owner/repo")
        assert not cache._body_references_slug(None, "owner/repo")

    def test_empty_results(self) -> None:
        assert not cache._body_references_slug({"results": []}, "owner/repo")


class TestGetRedis:
    @pytest.mark.anyio
    async def test_returns_none_when_disabled(
        self, with_disabled_service: None
    ) -> None:
        assert await cache.get_redis() is None

    @pytest.mark.anyio
    async def test_returns_client_when_enabled(
        self, with_fake_service: fakeredis.aioredis.FakeRedis
    ) -> None:
        result = await cache.get_redis()
        assert result is with_fake_service


class TestCloseRedis:
    @pytest.mark.anyio
    async def test_resets_client(
        self, with_fake_service: fakeredis.aioredis.FakeRedis
    ) -> None:
        assert cache._get_default_service()._client is with_fake_service
        await cache.close_redis()
        assert cache._get_default_service()._client is None


class TestCacheSearch:
    @pytest.mark.anyio
    async def test_caches_result_on_miss(
        self, with_fake_service: fakeredis.aioredis.FakeRedis
    ) -> None:
        call_count = 0

        @cache.cache_search(ttl=60)
        async def handler(body: dict[str, Any]) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            return {"results": [{"text": "hit"}]}

        result = await handler(body={"query": "test", "repos": None, "top_k": 10})
        assert result == {"results": [{"text": "hit"}]}
        assert call_count == 1

        keys = await with_fake_service.keys("ck:search:*")
        assert len(keys) == 1
        cached = await with_fake_service.get(keys[0])
        assert json.loads(cached) == {"results": [{"text": "hit"}]}

    @pytest.mark.anyio
    async def test_returns_cached_on_hit(
        self, with_fake_service: fakeredis.aioredis.FakeRedis
    ) -> None:
        call_count = 0

        @cache.cache_search()
        async def handler(body: dict[str, Any]) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            return {"results": [{"text": f"call-{call_count}"}]}

        r1 = await handler(body={"query": "test", "top_k": 10})
        r2 = await handler(body={"query": "test", "top_k": 10})
        assert r1 == r2
        assert call_count == 1

    @pytest.mark.anyio
    async def test_passthrough_when_disabled(self, with_disabled_service: None) -> None:
        call_count = 0

        @cache.cache_search()
        async def handler(body: dict[str, Any]) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            return {"results": []}

        await handler(body={"query": "test"})
        await handler(body={"query": "test"})
        assert call_count == 2

    @pytest.mark.anyio
    async def test_different_queries_both_cached(
        self, with_fake_service: fakeredis.aioredis.FakeRedis
    ) -> None:
        @cache.cache_search()
        async def handler(body: dict[str, Any]) -> dict[str, Any]:
            return {"query": body.get("query", ""), "results": []}

        await handler(body={"query": "alpha"})
        await handler(body={"query": "beta"})

        keys = await with_fake_service.keys("ck:search:*")
        assert len(keys) == 2


class TestInvalidateForSlug:
    @pytest.mark.anyio
    async def test_deletes_matching_keys(
        self, with_fake_service: fakeredis.aioredis.FakeRedis
    ) -> None:
        key_a = cache._make_key("q1", ["owner/repo"], 10)
        key_b = cache._make_key("q2", ["other/repo"], 10)

        await with_fake_service.set(
            key_a, json.dumps({"repos": ["owner/repo"], "results": []})
        )
        await with_fake_service.set(
            key_b, json.dumps({"repos": ["other/repo"], "results": []})
        )

        await cache.get_redis()
        service = cache._get_default_service()
        await service.invalidate_for_slug("owner/repo")

        assert await with_fake_service.exists(key_a) == 0
        assert await with_fake_service.exists(key_b) == 1

    @pytest.mark.anyio
    async def test_deletes_by_result_slug(
        self, with_fake_service: fakeredis.aioredis.FakeRedis
    ) -> None:
        key = cache._make_key("q", None, 5)
        await with_fake_service.set(
            key,
            json.dumps({"results": [{"slug": "owner/repo", "text": "x"}]}),
        )

        service = cache._get_default_service()
        await service.invalidate_for_slug("owner/repo")
        assert await with_fake_service.exists(key) == 0

    @pytest.mark.anyio
    async def test_preserves_unrelated_keys(
        self, with_fake_service: fakeredis.aioredis.FakeRedis
    ) -> None:
        key = cache._make_key("q", ["unrelated/repo"], 10)
        await with_fake_service.set(key, json.dumps({"repos": ["unrelated/repo"]}))

        service = cache._get_default_service()
        await service.invalidate_for_slug("owner/repo")
        assert await with_fake_service.exists(key) == 1

    @pytest.mark.anyio
    async def test_noop_when_disabled(self, with_disabled_service: None) -> None:
        service = cache._get_default_service()
        await service.invalidate_for_slug("anything")
