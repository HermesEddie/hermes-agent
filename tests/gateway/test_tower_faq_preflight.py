from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType
from gateway.run import GatewayRunner
from gateway.session import SessionSource
from gateway.tower_faq import (
    TowerFaqClient,
    TowerFaqMatch,
    TowerFaqQueryResult,
    TowerFaqSettings,
)


def _make_source(platform: Platform = Platform.FEISHU) -> SessionSource:
    return SessionSource(
        platform=platform,
        chat_id="chat-1",
        chat_type="dm",
        user_id="user-1",
        user_name="tester",
    )


def _make_event(text: str = "客户投诉赔付怎么处理？", platform: Platform = Platform.FEISHU) -> MessageEvent:
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=_make_source(platform),
        message_id="m1",
    )


def _make_runner(platform: Platform = Platform.FEISHU) -> GatewayRunner:
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={platform: PlatformConfig(enabled=True, token="***", extra={"app_id": "cli_test"})}
    )
    runner.adapters = {}
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._update_prompt_pending = {}
    runner._tower_faq_pending_clarifications = {}
    runner._draining = False
    runner._restart_requested = False
    runner._restart_task_started = False
    runner._restart_detached = False
    runner._restart_via_service = False
    runner._stop_task = None
    runner._exit_code = None
    runner._is_user_authorized = lambda _source: True
    runner.hooks = SimpleNamespace(emit=AsyncMock())
    return runner


def _enable_faq_env(monkeypatch):
    monkeypatch.setenv("TOWER_FAQ_ENABLED", "true")
    monkeypatch.setenv("TOWER_FAQ_QUERY_URL", "http://tower.test/api/internal/agent-workspace/faq-library/query")
    monkeypatch.setenv("AGENT_WORKSPACE_INTERNAL_TOKEN", "test-token")
    monkeypatch.setenv("TOWER_DEFAULT_TENANT_ID", "tenant-1")


def _result(answer_mode: str, match: TowerFaqMatch | None = None) -> TowerFaqQueryResult:
    return TowerFaqQueryResult(
        tenant_id="tenant-1",
        question_hash="question-hash",
        answer_mode_hint=answer_mode,
        matches=(match,) if match else (),
    )


def _clarify_result(*matches: TowerFaqMatch) -> TowerFaqQueryResult:
    return TowerFaqQueryResult(
        tenant_id="tenant-1",
        question_hash="question-hash",
        answer_mode_hint="clarify",
        matches=matches,
    )


def _match(**overrides) -> TowerFaqMatch:
    data = {
        "faq_id": "faq-1",
        "standard_question": "涉及客户投诉的赔付口径是什么？",
        "standard_answer": "客户投诉赔付需先由客服主管确认责任归因。",
        "score": 0.91,
        "source_policy": "客户投诉处理制度",
        "risk_level": "高",
        "allow_ai_direct_answer": True,
        "requires_human_handoff": False,
    }
    data.update(overrides)
    return TowerFaqMatch(**data)


@pytest.mark.asyncio
async def test_feishu_faq_direct_answer_bypasses_agent(monkeypatch):
    _enable_faq_env(monkeypatch)
    runner = _make_runner()
    runner._handle_message_with_agent = AsyncMock(side_effect=AssertionError("agent should not run"))

    async def fake_query(self, event, source):
        return _result("direct_answer", _match())

    monkeypatch.setattr(TowerFaqClient, "query", fake_query)

    response = await runner._handle_message(_make_event("查 FAQ：客户投诉赔付怎么处理？"))

    assert response == "客户投诉赔付需先由客服主管确认责任归因。"
    assert "来源制度" not in response
    assert "风险等级" not in response
    runner._handle_message_with_agent.assert_not_called()
    assert runner._running_agents == {}


@pytest.mark.asyncio
async def test_feishu_faq_handoff_bypasses_agent(monkeypatch):
    _enable_faq_env(monkeypatch)
    runner = _make_runner()
    runner._handle_message_with_agent = AsyncMock(side_effect=AssertionError("agent should not run"))

    async def fake_query(self, event, source):
        return _result("handoff_required", _match(requires_human_handoff=True))

    monkeypatch.setattr(TowerFaqClient, "query", fake_query)

    response = await runner._handle_message(_make_event("帮我查一下 FAQ，客户投诉赔付怎么处理？"))

    assert "根据公司相关管理规定" in response
    assert "需要人工确认处理" in response
    assert "AI 不直接给出口径" in response
    assert "来源制度" not in response
    assert "风险等级" not in response
    runner._handle_message_with_agent.assert_not_called()


@pytest.mark.asyncio
async def test_feishu_faq_clarify_stores_candidates_and_accepts_number(monkeypatch):
    _enable_faq_env(monkeypatch)
    runner = _make_runner()
    runner._handle_message_with_agent = AsyncMock(side_effect=AssertionError("agent should not run"))
    query = AsyncMock(
        return_value=_clarify_result(
            _match(
                faq_id="faq-payroll",
                standard_question="绩效工资是怎么核算发放的？",
                standard_answer="绩效工资会结合绩效结果和薪酬规则核算，并随工资发放。",
                risk_level="低",
            ),
            _match(
                faq_id="faq-salary-error",
                standard_question="发薪后工资有误怎么反馈？",
                standard_answer="发薪后如发现工资有误，请联系人事或财务核对。",
                risk_level="低",
            ),
        )
    )
    monkeypatch.setattr(TowerFaqClient, "query", query)

    first_response = await runner._handle_message(_make_event("查知识库：绩效工资怎么计算"))
    second_response = await runner._handle_message(_make_event("1"))

    assert "根据公司相关管理规定" in first_response
    assert "请回复序号" in first_response
    assert "1. 绩效工资是怎么核算发放的？" in first_response
    assert "根据公司相关管理规定" in second_response
    assert "绩效工资会结合绩效结果和薪酬规则核算" in second_response
    assert "来源制度" not in second_response
    assert "风险等级" not in second_response
    assert query.await_count == 1
    runner._handle_message_with_agent.assert_not_called()


@pytest.mark.asyncio
async def test_tower_faq_error_falls_back_to_agent(monkeypatch):
    _enable_faq_env(monkeypatch)
    runner = _make_runner()
    runner._handle_message_with_agent = AsyncMock(return_value="agent response")

    async def fake_query(self, event, source):
        raise RuntimeError("tower timeout")

    monkeypatch.setattr(TowerFaqClient, "query", fake_query)

    response = await runner._handle_message(_make_event("查 FAQ：客户投诉赔付怎么处理？"))

    assert response == "agent response"
    runner._handle_message_with_agent.assert_called_once()
    assert runner._running_agents == {}


@pytest.mark.asyncio
async def test_non_feishu_message_does_not_query_tower(monkeypatch):
    _enable_faq_env(monkeypatch)
    runner = _make_runner(Platform.TELEGRAM)
    runner._handle_message_with_agent = AsyncMock(return_value="agent response")
    query = AsyncMock(side_effect=AssertionError("Tower FAQ should not run"))
    monkeypatch.setattr(TowerFaqClient, "query", query)

    response = await runner._handle_message(_make_event(platform=Platform.TELEGRAM))

    assert response == "agent response"
    query.assert_not_called()


@pytest.mark.asyncio
async def test_feishu_normal_chat_does_not_preflight_tower_faq(monkeypatch):
    _enable_faq_env(monkeypatch)
    runner = _make_runner()
    runner._handle_message_with_agent = AsyncMock(return_value="agent response")
    query = AsyncMock(side_effect=AssertionError("Tower FAQ should not run for normal chat"))
    monkeypatch.setattr(TowerFaqClient, "query", query)

    response = await runner._handle_message(_make_event("客户投诉赔付怎么处理？"))

    assert response == "agent response"
    query.assert_not_called()


def test_tower_faq_settings_support_config_mapping(monkeypatch):
    for key in (
        "TOWER_FAQ_ENABLED",
        "TOWER_BASE_URL",
        "TOWER_API_BASE_URL",
        "TOWER_FAQ_QUERY_URL",
        "AGENT_WORKSPACE_INTERNAL_TOKEN",
        "SALES_TARGET_AGENT_INTERNAL_TOKEN",
        "TOWER_INTERNAL_TOKEN",
        "TOWER_DEFAULT_TENANT_ID",
        "TOWER_TENANT_ID",
        "AGENT_WORKSPACE_TENANT_ID",
    ):
        monkeypatch.delenv(key, raising=False)

    settings = TowerFaqSettings.from_config(
        {
            "tower": {
                "baseUrl": "http://tower.local",
                "internalToken": "token-from-config",
                "faq": {
                    "enabled": True,
                    "tenantByChat": {"chat-1": "tenant-chat"},
                    "tenantId": "tenant-default",
                },
            }
        }
    )

    assert settings.is_configured() is True
    assert settings.query_url == "http://tower.local/api/internal/agent-workspace/faq-library/query"
    assert settings.resolve_tenant_id(_make_source()) == "tenant-chat"


@pytest.mark.asyncio
async def test_tower_faq_query_question_uses_default_tenant_without_source():
    client = TowerFaqClient(
        TowerFaqSettings(
            enabled=True,
            query_url="http://tower.test/query",
            token="test-token",
            default_tenant_id="tenant-default",
        )
    )
    captured_body = {}

    async def fake_post_json(body):
        captured_body.update(body)
        return {"answerModeHint": "direct_answer", "matches": []}

    client._post_json = fake_post_json

    result = await client.query_question("查 FAQ：客户投诉赔付怎么处理？")

    assert result is not None
    assert result.tenant_id == "tenant-default"
    assert captured_body["tenantId"] == "tenant-default"
