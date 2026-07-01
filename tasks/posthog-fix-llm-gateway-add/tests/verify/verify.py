"""Run via:
    uv run --directory services/llm-gateway pytest --rootdir services/llm-gateway /tests/verify/verify.py
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import litellm
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import llm_gateway.api.anthropic as _anthropic_module
from llm_gateway.api.health import health_router
from llm_gateway.api.routes import router as gateway_router
from llm_gateway.rate_limiting.cost_throttles import (
    ProductCostThrottle,
    UserCostBurstThrottle,
    UserCostSustainedThrottle,
)
from llm_gateway.rate_limiting.runner import ThrottleRunner

# Fixtures re-implemented inline because pytest's conftest discovery walks
# UP from the test file's directory, and verify.py lives at
# /tests/verify/verify.py — outside the project's tests/ directory.
def _create_test_app(mock_db_pool: MagicMock) -> FastAPI:
    @asynccontextmanager
    async def test_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        app.state.db_pool = mock_db_pool
        app.state.redis = None
        app.state.throttle_runner = ThrottleRunner(
            throttles=[
                ProductCostThrottle(redis=None),
                UserCostBurstThrottle(redis=None),
                UserCostSustainedThrottle(redis=None),
            ]
        )
        yield

    app = FastAPI(title="LLM Gateway Verifier", lifespan=test_lifespan)
    app.include_router(health_router)
    app.include_router(gateway_router)
    return app


@pytest.fixture
def mock_db_pool() -> MagicMock:
    pool = MagicMock()
    conn = AsyncMock()
    # Default: no auth match (used by `client` fixture for unauthenticated tests).
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetchval = AsyncMock(return_value=1)
    pool.acquire = AsyncMock(return_value=conn)
    pool.release = AsyncMock()
    return pool


@pytest.fixture
def client(mock_db_pool: MagicMock) -> Generator[TestClient, None, None]:
    """Anonymous TestClient — no Authorization header → 401."""
    app = _create_test_app(mock_db_pool)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def authenticated_client(mock_db_pool: MagicMock) -> Generator[TestClient, None, None]:
    """Authenticated TestClient — `Bearer phx_test_key` header passes auth."""
    app = _create_test_app(mock_db_pool)

    conn = AsyncMock()
    conn.fetchrow = AsyncMock(
        return_value={
            "id": "key_id",
            "user_id": 1,
            "scopes": ["llm_gateway:read"],
            "current_team_id": 1,
            "distinct_id": "test-distinct-id",
        }
    )
    mock_db_pool.acquire = AsyncMock(return_value=conn)
    mock_db_pool.release = AsyncMock()

    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_auth_cache() -> Generator[None, None, None]:
    """Auth uses an lru_cache'd singleton AuthCache. Reset the cache
    between tests so the `client` fixture (no auth header) can't be
    fooled by a cache hit from an earlier authenticated test.

    The token "phx_test_key" hashes to one cache entry — once a
    test populates it, later unauthenticated tests still short-circuit
    on the missing header (no token → no cache lookup), so this fixture
    is belt-and-suspenders rather than strictly required.
    """
    from llm_gateway.auth.cache import get_auth_cache
    from llm_gateway.auth.service import get_auth_service

    yield
    # Clear after each test to avoid cross-test contamination.
    try:
        get_auth_cache.cache_clear()  # type: ignore[attr-defined]
    except AttributeError:
        pass
    try:
        get_auth_service.cache_clear()  # type: ignore[attr-defined]
    except AttributeError:
        pass


VALID_BODY: dict[str, Any] = {
    "model": "claude-3-5-sonnet-20241022",
    "messages": [{"role": "user", "content": "Hello"}],
}
AUTH_HEADERS = {"Authorization": "Bearer phx_test_key"}


def test_count_tokens_route_registered(authenticated_client: TestClient) -> None:
    """Without the fix, the gateway returns 404 for POST /v1/messages/count_tokens
    (the route doesn't exist). With the fix, the route is registered and
    returns something other than 404/405."""
    response = authenticated_client.post(
        "/v1/messages/count_tokens",
        json=VALID_BODY,
        headers=AUTH_HEADERS,
    )
    assert response.status_code != 404, (
        f"POST /v1/messages/count_tokens returned 404 — route is not "
        f"registered. The Claude Agent SDK's countTokensWithFallback "
        f"primary strategy is still failing → SDK falls back to a "
        f"max_tokens=1 completion call → spam $ai_generation events. "
        f"Body: {response.text[:500]}"
    )
    assert response.status_code != 405, (
        f"POST /v1/messages/count_tokens returned 405 (method not "
        f"allowed) — the path exists but POST isn't accepted. "
        f"Body: {response.text[:500]}"
    )


def test_count_tokens_product_route_registered(
    authenticated_client: TestClient,
) -> None:
    """A POST to /{product}/v1/messages/count_tokens with a valid product
    must be registered (mirrors the pre-existing /{product}/v1/messages
    convention)."""
    # `wizard` is a pre-existing valid product (services/llm-gateway/src/llm_gateway/products/config.py).
    response = authenticated_client.post(
        "/wizard/v1/messages/count_tokens",
        json=VALID_BODY,
        headers=AUTH_HEADERS,
    )
    assert response.status_code != 404, (
        f"POST /wizard/v1/messages/count_tokens returned 404 — the "
        f"product-prefixed count_tokens route is not registered. "
        f"Body: {response.text[:500]}"
    )
    assert response.status_code != 405, (
        f"POST /wizard/v1/messages/count_tokens returned 405. "
        f"Body: {response.text[:500]}"
    )


def test_count_tokens_invalid_product_returns_400(
    authenticated_client: TestClient,
) -> None:
    """An invalid product on /{product}/v1/messages/count_tokens must
    return 400 (matches the pre-existing /{product}/v1/messages product-
    validation contract)."""
    response = authenticated_client.post(
        "/invalid_product_xyz/v1/messages/count_tokens",
        json=VALID_BODY,
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 400, (
        f"POST /invalid_product_xyz/v1/messages/count_tokens returned "
        f"{response.status_code}, expected 400. Body: {response.text[:500]}"
    )


def test_count_tokens_requires_authentication(client: TestClient) -> None:
    """A POST to /v1/messages/count_tokens without an Authorization header
    must return 401. Mirrors the pre-existing /v1/messages auth contract."""
    response = client.post("/v1/messages/count_tokens", json=VALID_BODY)
    assert response.status_code == 401, (
        f"Unauthenticated POST /v1/messages/count_tokens returned "
        f"{response.status_code}, expected 401. Body: {response.text[:500]}"
    )


def test_count_tokens_validates_request_body_missing_model(
    authenticated_client: TestClient,
) -> None:
    """A POST with an empty body returns 422 with `model` in the error."""
    response = authenticated_client.post(
        "/v1/messages/count_tokens",
        json={},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 422, (
        f"POST with empty body returned {response.status_code}, "
        f"expected 422. Body: {response.text[:500]}"
    )
    assert "model" in response.text, (
        f"422 response should mention the missing `model` field. "
        f"Got: {response.text[:500]}"
    )


def test_count_tokens_validates_request_body_missing_messages(
    authenticated_client: TestClient,
) -> None:
    """A POST without `messages` returns 422 with `messages` in the error."""
    response = authenticated_client.post(
        "/v1/messages/count_tokens",
        json={"model": "claude-3-5-sonnet-20241022"},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 422, (
        f"POST without `messages` returned {response.status_code}, "
        f"expected 422. Body: {response.text[:500]}"
    )
    assert "messages" in response.text, (
        f"422 response should mention the missing `messages` field. "
        f"Got: {response.text[:500]}"
    )


# DISCRIMINATOR
def test_count_tokens_does_not_invoke_litellm(
    authenticated_client: TestClient,
) -> None:
    """count_tokens must NOT route through litellm: any litellm completion
    entry point fires PostHogCallback and spams an $ai_generation event.

    `litellm.anthropic_messages` is patched at TWO import sites:

    1. `litellm.anthropic_messages` — the source-module attribute. Any
       new code that does `import litellm; litellm.anthropic_messages(...)`
       sees the mock here.
    2. `llm_gateway.api.anthropic.litellm.anthropic_messages` — the
       reference held inside the existing handler module. Redundant with
       the first (same module attribute), but belt-and-suspenders against
       any odd import path.
    """
    with (
        patch.object(litellm, "anthropic_messages") as mock_litellm_source,
        patch.object(
            _anthropic_module.litellm, "anthropic_messages"
        ) as mock_litellm_anth,
        # Any litellm completion entry point fires PostHogCallback and spams
        # $ai_generation — not only anthropic_messages. Patch the unified
        # completion entry points too, so a fallback that token-counts via a
        # max_tokens=1 acompletion/completion call is caught as well.
        patch.object(litellm, "acompletion") as mock_litellm_acompletion,
        patch.object(litellm, "completion") as mock_litellm_completion,
    ):
        # Whatever the response status (the real impl might 503 because
        # no Anthropic API key is configured in the test env, or 200 if
        # it mocks the upstream proxy, or 500 because a broken impl
        # bombed out trying to JSON-serialize a MagicMock), the litellm
        # path must not have been invoked. Swallow server-side exceptions
        # so we always reach the assertions — the test_anthropic_messages
        # mock pattern auto-becomes AsyncMock for async targets, which
        # yields a non-serializable MagicMock response that crashes a
        # broken impl. We don't care about the crash; we care about the
        # call record.
        try:
            authenticated_client.post(
                "/v1/messages/count_tokens",
                json=VALID_BODY,
                headers=AUTH_HEADERS,
            )
        except Exception:
            # Server-side error from a broken impl re-using the existing
            # handler. The assertion below still distinguishes correct
            # from broken — the broken impl will have called the mock.
            pass

    mock_litellm_source.assert_not_called()
    mock_litellm_anth.assert_not_called()
    mock_litellm_acompletion.assert_not_called()
    mock_litellm_completion.assert_not_called()


# PASS-TO-PASS REGRESSION
def test_existing_messages_route_unchanged(
    authenticated_client: TestClient,
) -> None:
    """The pre-existing POST /v1/messages route must continue to work.

    With `litellm.anthropic_messages` mocked to return a typical
    Anthropic response shape, /v1/messages must still return 200 and
    forward the response body. Mirrors test_successful_request from
    services/llm-gateway/tests/test_anthropic.py.
    """
    mock_response = MagicMock()
    mock_response.model_dump = MagicMock(
        return_value={
            "id": "msg_existing_route_test",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Hello!"}],
            "model": "claude-3-5-sonnet-20241022",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
    )

    with patch(
        "llm_gateway.api.anthropic.litellm.anthropic_messages",
        return_value=mock_response,
    ):
        response = authenticated_client.post(
            "/v1/messages",
            json=VALID_BODY,
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 200, (
        f"Existing /v1/messages broken: status {response.status_code}. "
        f"Body: {response.text[:500]}"
    )
    data = response.json()
    assert data["id"] == "msg_existing_route_test"
    assert data["role"] == "assistant"
