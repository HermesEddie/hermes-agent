"""Tests for Tower-specific agent tools."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from gateway.tower_faq import TowerFaqClient, TowerFaqMatch, TowerFaqQueryResult


def _match(**overrides) -> TowerFaqMatch:
    data = {
        "faq_id": "faq-1",
        "standard_question": "客户投诉赔付怎么处理？",
        "standard_answer": "先确认责任归因，再按赔付制度执行。",
        "score": 0.93,
        "source_policy": "客户投诉处理制度",
        "risk_level": "低",
        "allow_ai_direct_answer": True,
        "requires_human_handoff": False,
    }
    data.update(overrides)
    return TowerFaqMatch(**data)


@pytest.mark.asyncio
async def test_tower_faq_query_tool_returns_structured_result(monkeypatch):
    from tools.tower_tools import tower_faq_query_tool

    async def fake_query_question(self, question, *, tenant_id=None, source=None):
        assert question == "客户投诉赔付怎么处理？"
        assert tenant_id == "tenant-1"
        assert source is None
        return TowerFaqQueryResult(
            tenant_id="tenant-1",
            question_hash="hash-1",
            answer_mode_hint="direct_answer",
            matches=(_match(),),
        )

    monkeypatch.setattr(TowerFaqClient, "query_question", fake_query_question)

    result = json.loads(
        await tower_faq_query_tool(
            {
                "question": "客户投诉赔付怎么处理？",
                "tenant_id": "tenant-1",
            }
        )
    )

    assert result["success"] is True
    assert result["answer_mode_hint"] == "direct_answer"
    assert "先确认责任归因，再按赔付制度执行。" in result["response_text"]
    assert result["matches"][0]["faq_id"] == "faq-1"


@pytest.mark.asyncio
async def test_tower_target_approval_review_tool_uses_review_runner(monkeypatch):
    from tools.tower_tools import tower_target_approval_review_tool

    run_mock = AsyncMock(
        return_value=[
            {
                "node_key": "asin-1",
                "result_status": "completed",
                "score": 4.3,
                "judgment": "manual_review",
                "summary": "需要人工复核",
                "note": "缺少销量证据",
                "missing_fields": ["actual_sales"],
                "evidence": [{"label": "issue_note", "value": "目标偏高"}],
                "recommended_action": "补充销量后复核",
            }
        ]
    )
    monkeypatch.setattr(
        "gateway.platforms.api_server.APIServerAdapter._run_tower_sales_target_review",
        run_mock,
    )

    result = json.loads(
        await tower_target_approval_review_tool(
            {
                "task_id": "task-1",
                "runtime_model": "test/model",
                "prompt_version": "v1",
                "review_guidance": {"prompt_text": "只根据输入证据审核目标。"},
                "context_items": [{"node_key": "asin-1", "issue_note": "目标偏高"}],
            }
        )
    )

    assert result["success"] is True
    assert result["dry_run"] is True
    assert result["items"][0]["judgment"] == "manual_review"
    assert result["items"][0]["recommended_action"] == "补充销量后复核"
    run_mock.assert_awaited_once()
