"""Tests for the Tower sales-target bridge on the API server adapter."""

from __future__ import annotations

import asyncio
import json
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
    def test_internal_token_prefers_agent_workspace_env(self, monkeypatch):
        adapter = _make_adapter()
        monkeypatch.setenv("SALES_TARGET_AGENT_INTERNAL_TOKEN", "old-token")
        monkeypatch.setenv("AGENT_WORKSPACE_INTERNAL_TOKEN", "new-token")

        headers = adapter._tower_internal_headers()

        assert adapter._tower_internal_token() == "new-token"
        assert headers["X-Agent-Workspace-Token"] == "new-token"
        assert headers["X-Sales-Target-Agent-Token"] == "new-token"

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

        fetch_mock = AsyncMock(
            return_value={
                "review_guidance": {
                    "prompt_text": "Tower policy: inventory_control with actual sales support should pass.",
                    "prompt_version": "sales_target_default_pass_v8",
                },
                "items": [{"node_key": "asin-1", "issue_note": "活动"}],
            }
        )
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
        assert run_mock.await_args.kwargs["review_guidance"]["prompt_version"] == "sales_target_default_pass_v8"

    @pytest.mark.asyncio
    async def test_job_prefers_payload_model_name_and_normalizes_for_provider(self, monkeypatch):
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
            "model_name": "glm-5.1",
        }

        fetch_mock = AsyncMock(return_value={"items": [{"node_key": "asin-1", "issue_note": "活动中"}]})
        run_mock = AsyncMock(
            return_value=[
                {
                    "node_key": "asin-1",
                    "result_status": "completed",
                    "score": 4.6,
                    "judgment": "pass",
                    "note": "动作信号成立",
                    "summary": "可通过",
                    "missing_fields": [],
                    "evidence": [{"label": "issue_note", "value": "活动中"}],
                    "recommended_action": "继续观察",
                }
            ]
        )
        callback_mock = AsyncMock()

        monkeypatch.setenv("TOWER_AGENT_REVIEW_RUNTIME_MODEL", "glm-5-turbo")

        with patch("gateway.run._resolve_runtime_agent_kwargs", return_value={"provider": "nous"}), patch.object(
            adapter, "_fetch_tower_context", fetch_mock
        ), patch.object(adapter, "_run_tower_sales_target_review", run_mock), patch.object(
            adapter, "_post_tower_results", callback_mock
        ):
            await adapter._run_tower_sales_target_review_job(payload, "req-override")

        assert run_mock.await_args.kwargs["runtime_model"] == "z-ai/glm-5.1"
        assert callback_mock.await_args.kwargs["model_name"] == "z-ai/glm-5.1"

    @pytest.mark.asyncio
    async def test_review_path_uses_no_tools_and_skips_context_files(self):
        adapter = _make_adapter()
        captured: dict[str, object] = {}

        class FakeAgent:
            def __init__(self, *args, **kwargs):
                captured["agent_kwargs"] = kwargs

            def run_conversation(self, user_message, conversation_history):
                captured["user_message"] = user_message
                captured["conversation_history"] = conversation_history
                return {
                    "final_response": json.dumps(
                        {
                            "items": [
                                {
                                    "node_key": "asin-1",
                                    "score": 4.2,
                                    "judgment": "pass",
                                    "note": "动作信号成立",
                                    "summary": "可通过",
                                    "transfer_impact_level": "low",
                                    "reasonability_level": "plausible",
                                    "review_reason_codes": ["actual_sales_supported"],
                                    "missing_fields": [],
                                    "evidence": [{"label": "issue_note", "value": "活动中"}],
                                    "recommended_action": "继续观察",
                                }
                            ]
                        },
                        ensure_ascii=False,
                    )
                }

        with patch("run_agent.AIAgent", FakeAgent), patch(
            "gateway.run._resolve_runtime_agent_kwargs",
            return_value={"provider": "nous", "api_key": "k", "base_url": "https://example.com", "api_mode": "chat_completions", "command": None, "args": [], "credential_pool": None},
        ), patch("gateway.run.GatewayRunner._load_fallback_model", return_value=None):
            result = await adapter._run_tower_sales_target_review(
                request_id="req-1",
                task_id="task-1",
                runtime_model="z-ai/glm-5.1",
                prompt_version="sales_target_default_pass_v4",
                context_items=[{"node_key": "asin-1", "issue_note": "活动中"}],
                review_guidance={
                    "prompt_text": "Tower policy says short action notes can pass.",
                    "prompt_version": "sales_target_default_pass_v8",
                },
            )

        assert result[0]["judgment"] == "pass"
        agent_kwargs = captured["agent_kwargs"]
        assert agent_kwargs["enabled_toolsets"] == []
        assert agent_kwargs["skip_context_files"] is True
        assert agent_kwargs["skip_memory"] is True
        assert agent_kwargs["persist_session"] is False
        assert "Tower policy says short action notes can pass." in agent_kwargs["ephemeral_system_prompt"]
        assert "review_reason_codes" in agent_kwargs["ephemeral_system_prompt"]
        assert result[0]["transfer_impact_level"] == "low"
        assert result[0]["reasonability_level"] == "plausible"
        assert result[0]["review_reason_codes"] == ["actual_sales_supported"]
        assert "sales_target_default_pass_v4" in captured["user_message"]
        assert "sales_target_default_pass_v8" in captured["user_message"]
        assert captured["conversation_history"] == []

    @pytest.mark.asyncio
    async def test_review_repairs_missing_node_keys(self):
        adapter = _make_adapter()
        captured: dict[str, int] = {"calls": 0}

        class FakeAgent:
            def __init__(self, *args, **kwargs):
                pass

            def run_conversation(self, user_message, conversation_history):
                captured["calls"] += 1
                if captured["calls"] == 1:
                    return {
                        "final_response": json.dumps(
                            {
                                "items": [
                                    {
                                        "node_key": "asin-1",
                                        "score": 4.2,
                                        "judgment": "pass",
                                        "note": "动作信号成立",
                                        "summary": "可通过",
                                        "missing_fields": [],
                                        "evidence": [{"label": "issue_note", "value": "活动中"}],
                                        "recommended_action": "继续观察",
                                    }
                                ]
                            },
                            ensure_ascii=False,
                        )
                    }
                return {
                    "final_response": json.dumps(
                        {
                            "items": [
                                {
                                    "node_key": "asin-1",
                                    "score": 4.2,
                                    "judgment": "pass",
                                    "note": "动作信号成立",
                                    "summary": "可通过",
                                    "missing_fields": [],
                                    "evidence": [{"label": "issue_note", "value": "活动中"}],
                                    "recommended_action": "继续观察",
                                },
                                {
                                    "node_key": "asin-2",
                                    "score": 3.0,
                                    "judgment": "need_more_info",
                                    "note": "需要补充关键判断",
                                    "summary": "关键判断被卡住",
                                    "missing_fields": ["历史表现"],
                                    "evidence": [{"label": "issue_type", "value": "promotion"}],
                                    "recommended_action": "补充历史表现后重评",
                                },
                            ]
                        },
                        ensure_ascii=False,
                    )
                }

        with patch("run_agent.AIAgent", FakeAgent), patch(
            "gateway.run._resolve_runtime_agent_kwargs",
            return_value={"provider": "nous", "api_key": "k", "base_url": "https://example.com", "api_mode": "chat_completions", "command": None, "args": [], "credential_pool": None},
        ), patch("gateway.run.GatewayRunner._load_fallback_model", return_value=None):
            result = await adapter._run_tower_sales_target_review(
                request_id="req-1",
                task_id="task-1",
                runtime_model="z-ai/glm-5.1",
                prompt_version="sales_target_default_pass_v4",
                context_items=[
                    {"node_key": "asin-1", "issue_note": "活动中"},
                    {"node_key": "asin-2", "issue_type": "promotion"},
                ],
            )

        assert captured["calls"] == 3
        assert [item["node_key"] for item in result] == ["asin-1", "asin-2"]
        assert result[0]["judgment"] == "pass"
        assert result[1]["judgment"] == "need_more_info"

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
