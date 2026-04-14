"""Tests for the Tower sales-target bridge on the API server adapter."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import PlatformConfig
from gateway.platforms.api_server import (
    APIServerAdapter,
    cors_middleware,
    security_headers_middleware,
)


def _make_adapter(api_key: str = "") -> APIServerAdapter:
    extra = {"key": api_key} if api_key else {}
    return APIServerAdapter(PlatformConfig(enabled=True, extra=extra))


def _create_app(adapter: APIServerAdapter) -> web.Application:
    mws = [mw for mw in (cors_middleware, security_headers_middleware) if mw is not None]
    app = web.Application(middlewares=mws)
    app["api_server_adapter"] = adapter
    app.router.add_post("/api/tower/sales-target/agent-review", adapter._handle_tower_sales_target_review)
    return app


class TestTowerSalesTargetRoute:
    @pytest.mark.asyncio
    async def test_requires_auth_when_api_key_configured(self):
        adapter = _make_adapter(api_key="secret")
        app = _create_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post("/api/tower/sales-target/agent-review", json={})
            assert resp.status == 401

    @pytest.mark.asyncio
    async def test_rejects_when_internal_token_missing(self):
        adapter = _make_adapter(api_key="secret")
        app = _create_app(adapter)
        body = {
            "task_id": "task-1",
            "tenant_id": "tenant-1",
            "view_id": "view-1",
            "start_month": "2026-04",
            "node_keys": ["asin-1"],
            "context_url": "http://tower/context",
            "callback_url": "http://tower/callback",
        }
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(
                "/api/tower/sales-target/agent-review",
                json=body,
                headers={"Authorization": "Bearer secret"},
            )
            assert resp.status == 500
            data = await resp.json()
            assert data["error"]["code"] == "missing_internal_token"

    @pytest.mark.asyncio
    async def test_accepts_request_and_schedules_background_job(self, monkeypatch):
        adapter = _make_adapter(api_key="secret")
        app = _create_app(adapter)
        body = {
            "task_id": "task-1",
            "tenant_id": "tenant-1",
            "view_id": "view-1",
            "start_month": "2026-04",
            "node_keys": ["asin-1"],
            "context_url": "http://tower/context",
            "callback_url": "http://tower/callback",
        }

        monkeypatch.setenv("SALES_TARGET_AGENT_INTERNAL_TOKEN", "tower-token")
        background_mock = AsyncMock()

        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_run_tower_sales_target_review_job", background_mock):
                resp = await cli.post(
                    "/api/tower/sales-target/agent-review",
                    json=body,
                    headers={"Authorization": "Bearer secret"},
                )
                assert resp.status == 202
                data = await resp.json()
                assert data["task_id"] == "task-1"
                assert data["status"] == "accepted"
                await asyncio.sleep(0)
                background_mock.assert_awaited_once()


class TestTowerSalesTargetJob:
    @pytest.mark.asyncio
    async def test_job_fetches_context_runs_model_and_callbacks(self):
        adapter = _make_adapter()
        payload = {
            "task_id": "task-1",
            "tenant_id": "tenant-1",
            "view_id": "view-1",
            "start_month": "2026-04",
            "node_keys": ["asin-1"],
            "context_url": "http://tower/context",
            "callback_url": "http://tower/callback",
            "prompt_version": "v1",
        }

        fetch_mock = AsyncMock(return_value={"items": [{"node_key": "asin-1", "issue_note": "活动"}]})
        run_mock = AsyncMock(
            return_value=[
                {
                    "node_key": "asin-1",
                    "result_status": "completed",
                    "score": 4.2,
                    "judgment": "pass",
                    "note": "理由充分",
                    "summary": "可通过",
                    "missing_fields": [],
                    "evidence": [{"label": "issue_note", "value": "活动"}],
                    "recommended_action": "继续观察",
                }
            ]
        )
        callback_mock = AsyncMock()

        with patch.object(adapter, "_fetch_tower_context", fetch_mock), patch.object(
            adapter, "_run_tower_sales_target_review", run_mock
        ), patch.object(adapter, "_post_tower_results", callback_mock):
            await adapter._run_tower_sales_target_review_job(payload, "req-1")

        fetch_mock.assert_awaited_once()
        run_mock.assert_awaited_once()
        callback_mock.assert_awaited_once()
        callback_items = callback_mock.await_args.kwargs["items"]
        assert callback_items[0]["judgment"] == "pass"
        assert callback_items[0]["node_key"] == "asin-1"

    @pytest.mark.asyncio
    async def test_job_callbacks_failed_items_when_review_raises(self):
        adapter = _make_adapter()
        payload = {
            "task_id": "task-1",
            "tenant_id": "tenant-1",
            "view_id": "view-1",
            "start_month": "2026-04",
            "node_keys": ["asin-1", "asin-2"],
            "context_url": "http://tower/context",
            "callback_url": "http://tower/callback",
            "prompt_version": "v1",
        }

        fetch_mock = AsyncMock(return_value={"items": [{"node_key": "asin-1"}, {"node_key": "asin-2"}]})
        callback_mock = AsyncMock()

        with patch.object(adapter, "_fetch_tower_context", fetch_mock), patch.object(
            adapter, "_run_tower_sales_target_review", AsyncMock(side_effect=RuntimeError("model exploded"))
        ), patch.object(adapter, "_post_tower_results", callback_mock):
            await adapter._run_tower_sales_target_review_job(payload, "req-2")

        callback_items = callback_mock.await_args.kwargs["items"]
        assert len(callback_items) == 2
        assert all(item["result_status"] == "failed" for item in callback_items)
        assert "model exploded" in callback_items[0]["summary"]
