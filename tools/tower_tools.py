"""Tower system tools for FAQ lookup and target approval review."""

from __future__ import annotations

from collections.abc import Mapping
import json
import os
import uuid
from typing import Any

from hermes_constants import get_hermes_home
from tools.registry import registry


_DEFAULT_TARGET_REVIEW_PROMPT = (
    "Review Tower target approval items using only the supplied context. "
    "Return pass only when the evidence clearly supports the target. "
    "Return manual_review when important evidence is missing or ambiguous. "
    "Return reject when the supplied evidence contradicts the target or shows "
    "that the target is unreasonable."
)


def _json(data: Mapping[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False)


def _string(value: Any) -> str:
    return str(value or "").strip()


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _load_config_mapping() -> Mapping[str, Any]:
    config_path = get_hermes_home() / "config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml

        with open(config_path, encoding="utf-8") as file:
            data = yaml.safe_load(file) or {}
            return data if isinstance(data, Mapping) else {}
    except Exception:
        return {}


def _model_from_config(config: Mapping[str, Any]) -> str:
    model_cfg = config.get("model")
    if isinstance(model_cfg, str):
        return model_cfg.strip()
    if isinstance(model_cfg, Mapping):
        return _string(model_cfg.get("default") or model_cfg.get("model"))
    return ""


def _matches_to_dicts(matches: tuple[Any, ...]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for match in matches:
        items.append(
            {
                "faq_id": match.faq_id,
                "standard_question": match.standard_question,
                "standard_answer": match.standard_answer,
                "score": match.score,
                "source_policy": match.source_policy,
                "risk_level": match.risk_level,
                "allow_ai_direct_answer": match.allow_ai_direct_answer,
                "requires_human_handoff": match.requires_human_handoff,
            }
        )
    return items


async def tower_faq_query_tool(args: dict[str, Any], **_: Any) -> str:
    """Query Tower FAQ explicitly at the agent's discretion."""
    from gateway.tower_faq import TowerFaqClient

    question = _string(args.get("question"))
    if not question:
        return _json({"success": False, "error": "question is required", "code": "missing_question"})

    tenant_id = _string(args.get("tenant_id") or args.get("tenantId"))
    client = TowerFaqClient.from_config(_load_config_mapping())
    result = await client.query_question(question, tenant_id=tenant_id)
    if result is None:
        code = "tower_faq_no_result"
        if not client.settings.is_configured():
            code = "tower_faq_not_configured"
        elif not tenant_id:
            code = "tower_faq_missing_tenant"
        return _json(
            {
                "success": False,
                "error": "Tower FAQ query did not return a result.",
                "code": code,
            }
        )

    return _json(
        {
            "success": True,
            "tenant_id": result.tenant_id,
            "question_hash": result.question_hash,
            "answer_mode_hint": result.answer_mode_hint,
            "response_text": result.gateway_response_text(polish_answers=client.settings.polish_answers),
            "matches": _matches_to_dicts(result.matches),
        }
    )


def _review_guidance_from_args(args: Mapping[str, Any]) -> dict[str, Any]:
    guidance = dict(_mapping(args.get("review_guidance") or args.get("reviewGuidance")))
    prompt_text = _string(args.get("prompt_text") or args.get("promptText") or guidance.get("prompt_text"))
    guidance["prompt_text"] = prompt_text or _DEFAULT_TARGET_REVIEW_PROMPT
    return guidance


def _context_items_from_args(args: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_items = args.get("context_items") or args.get("contextItems") or args.get("target_items") or []
    if not isinstance(raw_items, list):
        return []
    return [dict(item) for item in raw_items if isinstance(item, Mapping)]


def _runtime_model_from_args(args: Mapping[str, Any], config: Mapping[str, Any]) -> str:
    return (
        _string(args.get("runtime_model") or args.get("runtimeModel") or args.get("model_name") or args.get("modelName"))
        or _string(os.getenv("TOWER_AGENT_REVIEW_RUNTIME_MODEL"))
        or _model_from_config(config)
    )


async def tower_target_approval_review_tool(args: dict[str, Any], **_: Any) -> str:
    """Run a review-only Tower target approval recommendation."""
    from gateway.config import PlatformConfig
    from gateway.platforms.api_server import APIServerAdapter

    context_items = _context_items_from_args(args)
    if not context_items:
        return _json(
            {
                "success": False,
                "error": "context_items must contain at least one review item.",
                "code": "missing_context_items",
            }
        )

    config = _load_config_mapping()
    runtime_model = _runtime_model_from_args(args, config)
    if not runtime_model:
        return _json(
            {
                "success": False,
                "error": "runtime_model is required when no default model is configured.",
                "code": "missing_runtime_model",
            }
        )

    if args.get("dry_run") is False:
        return _json(
            {
                "success": False,
                "error": "This tool is review-only and does not approve or callback Tower.",
                "code": "mutation_not_supported",
            }
        )

    task_id = _string(args.get("task_id") or args.get("taskId")) or f"tower_tool_{uuid.uuid4().hex}"
    prompt_version = _string(args.get("prompt_version") or args.get("promptVersion")) or "tool_v1"
    request_id = _string(args.get("request_id") or args.get("requestId")) or f"tower_tool_{uuid.uuid4().hex}"
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={"model_name": runtime_model}))
    items = await adapter._run_tower_sales_target_review(
        request_id=request_id,
        task_id=task_id,
        runtime_model=runtime_model,
        prompt_version=prompt_version,
        context_items=context_items,
        review_guidance=_review_guidance_from_args(args),
    )
    return _json(
        {
            "success": True,
            "dry_run": True,
            "task_id": task_id,
            "request_id": request_id,
            "runtime_model": runtime_model,
            "prompt_version": prompt_version,
            "items": items,
        }
    )


registry.register(
    name="tower_faq_query",
    toolset="tower",
    schema={
        "name": "tower_faq_query",
        "description": (
            "Query the Tower FAQ library for explicit FAQ/knowledge-base questions. "
            "Use only when the user asks to query FAQ or knowledge base."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The complete FAQ question, including relevant session context.",
                },
                "tenant_id": {
                    "type": "string",
                    "description": "Tower tenant id. Optional when configured by Hermes profile.",
                },
            },
            "required": ["question"],
        },
    },
    handler=tower_faq_query_tool,
    is_async=True,
    description="Query Tower FAQ library",
    emoji="🏢",
)

registry.register(
    name="tower_target_approval_review",
    toolset="tower",
    schema={
        "name": "tower_target_approval_review",
        "description": (
            "Review Tower target approval items and return recommendations only. "
            "This tool does not approve, reject, callback, or mutate Tower records."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "context_items": {
                    "type": "array",
                    "description": "Tower review items. Each item must include node_key and evidence fields.",
                    "items": {"type": "object"},
                },
                "review_guidance": {
                    "type": "object",
                    "description": "Optional Tower-provided review guidance. prompt_text is recommended.",
                },
                "prompt_text": {
                    "type": "string",
                    "description": "Optional review instruction when review_guidance.prompt_text is absent.",
                },
                "task_id": {"type": "string"},
                "prompt_version": {"type": "string"},
                "runtime_model": {"type": "string"},
                "dry_run": {
                    "type": "boolean",
                    "description": "Must remain true. The tool is review-only.",
                },
            },
            "required": ["context_items"],
        },
    },
    handler=tower_target_approval_review_tool,
    is_async=True,
    description="Review Tower target approval items",
    emoji="🏢",
)
