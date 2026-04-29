"""Tower FAQ query integration for gateway preflight answers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import logging
import os
import re
import time
from typing import Any

from gateway.config import Platform
from gateway.platforms.base import MessageEvent, MessageType
from gateway.session import SessionSource

logger = logging.getLogger(__name__)


_DEFAULT_QUERY_PATH = "/api/internal/agent-workspace/faq-library/query"
_ALLOWED_MODES = {"keyword", "vector", "hybrid"}
_ANSWER_DIRECT = "direct_answer"
_ANSWER_HANDOFF = "handoff_required"
_ANSWER_NOT_ALLOWED = "not_allowed"
_ANSWER_CLARIFY = "clarify"
_DEFAULT_CLARIFY_TTL_SECONDS = 300
_CHOICE_DIGITS = {
    "1": 1,
    "１": 1,
    "一": 1,
    "2": 2,
    "２": 2,
    "二": 2,
    "3": 3,
    "３": 3,
    "三": 3,
}
_CHOICE_RE = re.compile(r"^\s*([1-3１-３一二三])\s*[.。)、）]?\s*$")
_FAQ_TERM_RE = re.compile(
    r"(faq|f\s*a\s*q|tower\s+faq|知识库|标准问题|标准问答|问答库|制度库|faq库)",
    re.IGNORECASE,
)
_FAQ_QUERY_VERB_RE = re.compile(r"(查|查询|检索|搜索|搜|找|看一下|查一下|帮我查|帮忙查)")


@dataclass(frozen=True)
class TowerFaqSettings:
    enabled: bool = False
    query_url: str = ""
    token: str = ""
    default_tenant_id: str = ""
    retrieval_mode: str = "hybrid"
    limit: int = 3
    timeout_seconds: float = 5.0
    max_question_chars: int = 1200
    clarify_ttl_seconds: int = _DEFAULT_CLARIFY_TTL_SECONDS
    polish_answers: bool = True
    tenant_by_chat: Mapping[str, str] = field(default_factory=dict)
    tenant_by_user: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_config(cls, config: Mapping[str, Any] | None) -> "TowerFaqSettings":
        config = config or {}
        tower_cfg = _mapping(config.get("tower"))
        faq_cfg = _mapping(tower_cfg.get("faq"))

        enabled = _coerce_bool(
            os.getenv("TOWER_FAQ_ENABLED"),
            default=_coerce_bool(faq_cfg.get("enabled"), default=False),
        )
        base_url = _string_env(
            "TOWER_BASE_URL",
            "TOWER_API_BASE_URL",
            fallback=_string_value(tower_cfg.get("base_url") or tower_cfg.get("baseUrl")),
        )
        query_url = _string_env(
            "TOWER_FAQ_QUERY_URL",
            fallback=_string_value(faq_cfg.get("query_url") or faq_cfg.get("queryUrl")),
        )
        if not query_url and base_url:
            query_url = f"{base_url.rstrip('/')}{_DEFAULT_QUERY_PATH}"

        token = _string_env(
            "AGENT_WORKSPACE_INTERNAL_TOKEN",
            "SALES_TARGET_AGENT_INTERNAL_TOKEN",
            "TOWER_INTERNAL_TOKEN",
            fallback=_string_value(tower_cfg.get("internal_token") or tower_cfg.get("internalToken")),
        )
        default_tenant_id = _string_env(
            "TOWER_DEFAULT_TENANT_ID",
            "TOWER_TENANT_ID",
            "AGENT_WORKSPACE_TENANT_ID",
            fallback=_string_value(
                faq_cfg.get("tenant_id")
                or faq_cfg.get("tenantId")
                or tower_cfg.get("tenant_id")
                or tower_cfg.get("tenantId")
            ),
        )
        retrieval_mode = _string_env(
            "TOWER_FAQ_RETRIEVAL_MODE",
            fallback=_string_value(faq_cfg.get("retrieval_mode") or faq_cfg.get("retrievalMode") or "hybrid"),
        )
        if retrieval_mode not in _ALLOWED_MODES:
            retrieval_mode = "hybrid"

        return cls(
            enabled=enabled,
            query_url=query_url,
            token=token,
            default_tenant_id=default_tenant_id,
            retrieval_mode=retrieval_mode,
            limit=_coerce_int(os.getenv("TOWER_FAQ_LIMIT"), faq_cfg.get("limit"), default=3, minimum=1, maximum=20),
            timeout_seconds=_coerce_float(
                os.getenv("TOWER_FAQ_TIMEOUT_SECONDS"),
                faq_cfg.get("timeout_seconds") or faq_cfg.get("timeoutSeconds"),
                default=5.0,
                minimum=0.5,
                maximum=30.0,
            ),
            max_question_chars=_coerce_int(
                os.getenv("TOWER_FAQ_MAX_QUESTION_CHARS"),
                faq_cfg.get("max_question_chars") or faq_cfg.get("maxQuestionChars"),
                default=1200,
                minimum=20,
                maximum=8000,
            ),
            clarify_ttl_seconds=_coerce_int(
                os.getenv("TOWER_FAQ_CLARIFY_TTL_SECONDS"),
                faq_cfg.get("clarify_ttl_seconds") or faq_cfg.get("clarifyTtlSeconds"),
                default=_DEFAULT_CLARIFY_TTL_SECONDS,
                minimum=30,
                maximum=3600,
            ),
            polish_answers=_coerce_bool(
                os.getenv("TOWER_FAQ_POLISH_ENABLED"),
                default=_coerce_bool(faq_cfg.get("polish_answers") or faq_cfg.get("polishAnswers"), default=True),
            ),
            tenant_by_chat=_string_map(faq_cfg.get("tenant_by_chat") or faq_cfg.get("tenantByChat")),
            tenant_by_user=_string_map(faq_cfg.get("tenant_by_user") or faq_cfg.get("tenantByUser")),
        )

    def is_configured(self) -> bool:
        return bool(self.enabled and self.query_url and self.token)

    def resolve_tenant_id(self, source: SessionSource) -> str:
        return (
            self.tenant_by_chat.get(source.chat_id or "")
            or self.tenant_by_user.get(source.user_id or "")
            or self.default_tenant_id
        )


@dataclass(frozen=True)
class TowerFaqMatch:
    faq_id: str
    standard_question: str
    standard_answer: str
    score: float
    source_policy: str = ""
    risk_level: str = ""
    allow_ai_direct_answer: bool = False
    requires_human_handoff: bool = False

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "TowerFaqMatch":
        return cls(
            faq_id=_string_value(_first(payload, "faqId", "faq_id")),
            standard_question=_string_value(_first(payload, "standardQuestion", "standard_question")),
            standard_answer=_string_value(_first(payload, "standardAnswer", "standard_answer")),
            score=_coerce_float(_first(payload, "score"), default=0.0, minimum=0.0, maximum=1.0),
            source_policy=_string_value(_first(payload, "sourcePolicy", "source_policy")),
            risk_level=_string_value(_first(payload, "riskLevel", "risk_level")),
            allow_ai_direct_answer=_coerce_bool(
                _first(payload, "allowAiDirectAnswer", "allow_ai_direct_answer"),
                default=False,
            ),
            requires_human_handoff=_coerce_bool(
                _first(payload, "requiresHumanHandoff", "requires_human_handoff"),
                default=False,
            ),
        )


@dataclass(frozen=True)
class TowerFaqQueryResult:
    tenant_id: str
    question_hash: str
    answer_mode_hint: str
    matches: tuple[TowerFaqMatch, ...] = ()

    @classmethod
    def from_payload(
        cls,
        *,
        tenant_id: str,
        question: str,
        payload: Mapping[str, Any],
    ) -> "TowerFaqQueryResult":
        raw_matches = _first(payload, "matches") or []
        matches = tuple(
            TowerFaqMatch.from_payload(item)
            for item in raw_matches
            if isinstance(item, Mapping)
        )
        return cls(
            tenant_id=tenant_id,
            question_hash=_hash_text(question),
            answer_mode_hint=_string_value(_first(payload, "answerModeHint", "answer_mode_hint")),
            matches=matches,
        )

    @property
    def top_match(self) -> TowerFaqMatch | None:
        return self.matches[0] if self.matches else None

    def gateway_response_text(self, *, polish_answers: bool = True) -> str | None:
        top = self.top_match
        if top is None:
            return None
        if self.answer_mode_hint == _ANSWER_CLARIFY:
            return _clarify_text(self.matches)
        return _match_response_text(
            top,
            answer_mode_hint=self.answer_mode_hint,
            polish_answers=polish_answers,
        )


@dataclass(frozen=True)
class TowerFaqPendingClarification:
    tenant_id: str
    question_hash: str
    matches: tuple[TowerFaqMatch, ...]
    expires_at: float

    def is_expired(self) -> bool:
        return time.time() >= self.expires_at

    def response_for_choice(self, choice: int, *, polish_answers: bool) -> str | None:
        if choice < 1 or choice > len(self.matches):
            return None
        return _match_response_text(
            self.matches[choice - 1],
            answer_mode_hint=_ANSWER_DIRECT,
            polish_answers=polish_answers,
        )


def _match_response_text(
    match: TowerFaqMatch,
    *,
    answer_mode_hint: str,
    polish_answers: bool,
) -> str | None:
    if match.requires_human_handoff or answer_mode_hint == _ANSWER_HANDOFF:
        return _handoff_text(match)
    if not match.allow_ai_direct_answer or answer_mode_hint == _ANSWER_NOT_ALLOWED:
        return _not_allowed_text(match)
    if answer_mode_hint == _ANSWER_DIRECT:
        return _direct_answer_text(match, polish_answers=polish_answers)
    if answer_mode_hint == _ANSWER_CLARIFY:
        return _clarify_text((match,))
    return None


class TowerFaqClient:
    def __init__(self, settings: TowerFaqSettings):
        self.settings = settings

    @classmethod
    def from_config(cls, config: Mapping[str, Any] | None) -> "TowerFaqClient":
        return cls(TowerFaqSettings.from_config(config))

    def should_query(self, event: MessageEvent, source: SessionSource) -> bool:
        if source.platform != Platform.FEISHU:
            return False
        if event.message_type != MessageType.TEXT or event.get_command():
            return False
        if event.media_urls or event.media_types:
            return False
        question = (event.text or "").strip()
        if not is_explicit_tower_faq_request(question):
            return False
        if not question or len(question) > self.settings.max_question_chars:
            return False
        if not self.settings.is_configured():
            return False
        return bool(self.settings.resolve_tenant_id(source))

    def pending_key(self, source: SessionSource) -> str:
        return ":".join(
            [
                source.platform.value if source.platform else "",
                source.chat_id or "",
                source.user_id or "",
                self.settings.resolve_tenant_id(source),
            ]
        )

    def resolve_pending_response(
        self,
        event: MessageEvent,
        source: SessionSource,
        pending: dict[str, TowerFaqPendingClarification],
    ) -> str | None:
        if source.platform != Platform.FEISHU:
            return None
        if event.message_type != MessageType.TEXT or event.get_command():
            return None
        if event.media_urls or event.media_types:
            return None
        key = self.pending_key(source)
        state = pending.get(key)
        if state is None:
            return None
        if state.is_expired():
            pending.pop(key, None)
            return None
        choice = _parse_choice_text(event.text or "")
        if choice is None:
            pending.pop(key, None)
            return None
        if choice > len(state.matches):
            return f"请回复 1-{len(state.matches)} 中的一个数字来选择对应的标准问题。"
        pending.pop(key, None)
        return state.response_for_choice(choice, polish_answers=self.settings.polish_answers)

    def update_pending_clarification(
        self,
        source: SessionSource,
        result: TowerFaqQueryResult,
        pending: dict[str, TowerFaqPendingClarification],
    ) -> None:
        key = self.pending_key(source)
        if result.answer_mode_hint != _ANSWER_CLARIFY or not result.matches:
            pending.pop(key, None)
            return
        pending[key] = TowerFaqPendingClarification(
            tenant_id=result.tenant_id,
            question_hash=result.question_hash,
            matches=result.matches[: self.settings.limit],
            expires_at=time.time() + self.settings.clarify_ttl_seconds,
        )

    async def query_question(
        self,
        question: str,
        *,
        tenant_id: str = "",
        source: SessionSource | None = None,
    ) -> TowerFaqQueryResult | None:
        question = (question or "").strip()
        tenant_id = (
            tenant_id
            or (self.settings.resolve_tenant_id(source) if source else "")
            or self.settings.default_tenant_id
        ).strip()
        if not question or len(question) > self.settings.max_question_chars:
            return None
        if not self.settings.is_configured() or not tenant_id:
            return None
        body = {
            "tenantId": tenant_id,
            "question": question,
            "limit": self.settings.limit,
            "retrievalMode": self.settings.retrieval_mode,
            "filters": {},
        }
        try:
            payload = await self._post_json(body)
        except Exception as exc:
            logger.warning("Tower FAQ query failed: %s", exc)
            return None
        result = TowerFaqQueryResult.from_payload(
            tenant_id=tenant_id,
            question=question,
            payload=payload,
        )
        if source is not None:
            _log_result(source, result)
        else:
            logger.info(
                "Tower FAQ tool result: tenant=%s question_hash=%s mode=%s faq_id=%s score=%.4f",
                result.tenant_id,
                result.question_hash,
                result.answer_mode_hint or "unknown",
                result.top_match.faq_id if result.top_match else "",
                result.top_match.score if result.top_match else 0.0,
            )
        return result

    async def query(self, event: MessageEvent, source: SessionSource) -> TowerFaqQueryResult | None:
        return await self.query_question(event.text or "", source=source)

    async def _post_json(self, body: Mapping[str, Any]) -> Mapping[str, Any]:
        import aiohttp

        headers = {
            "Content-Type": "application/json",
            "X-Agent-Workspace-Token": self.settings.token,
            "X-Sales-Target-Agent-Token": self.settings.token,
        }
        timeout = aiohttp.ClientTimeout(total=self.settings.timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(self.settings.query_url, json=dict(body), headers=headers) as response:
                if response.status >= 400:
                    text = await response.text()
                    raise RuntimeError(f"Tower FAQ query HTTP {response.status}: {text[:300]}")
                data = await response.json()
                if not isinstance(data, Mapping):
                    raise RuntimeError("Tower FAQ query returned non-object JSON")
                return data


def _direct_answer_text(match: TowerFaqMatch, *, polish_answers: bool) -> str:
    answer = _clean_public_answer(match.standard_answer)
    if not polish_answers or _is_high_risk(match):
        return answer
    return _polish_public_answer(match, answer)


def _handoff_text(match: TowerFaqMatch) -> str:
    return "根据公司相关管理规定，该问题需要人工确认处理，AI 不直接给出口径。请联系相关负责人继续确认。"


def _not_allowed_text(match: TowerFaqMatch) -> str:
    return "根据公司相关管理规定，该问题当前不允许 AI 直接回答。请联系人工确认标准口径。"


def _clarify_text(matches: tuple[TowerFaqMatch, ...]) -> str | None:
    candidates = [match.standard_question for match in matches[:3] if match.standard_question]
    if not candidates:
        return None
    lines = ["根据公司相关管理规定，已检索到以下可能相关的标准问题。请回复序号确认："]
    lines.extend(f"{index}. {question}" for index, question in enumerate(candidates, start=1))
    return "\n".join(lines)


def _clean_public_answer(answer: str) -> str:
    lines = [_string_value(line) for line in _string_value(answer).splitlines()]
    return "\n".join(line for line in lines if line)


def _polish_public_answer(match: TowerFaqMatch, answer: str) -> str:
    if not answer:
        return answer
    variants = (
        "根据公司相关管理规定，答复如下：\n{answer}",
        "根据公司相关管理规定，相关处理口径如下：\n{answer}",
        "依据公司相关管理规定，处理要求如下：\n{answer}",
        "按照公司相关管理规定，可按以下口径执行：\n{answer}",
    )
    digest = hashlib.sha256((match.faq_id or match.standard_question).encode("utf-8")).hexdigest()
    template = variants[int(digest[:2], 16) % len(variants)]
    return template.format(answer=answer)


def _is_high_risk(match: TowerFaqMatch) -> bool:
    risk = match.risk_level.strip().casefold()
    return "高" in risk or risk == "high"


def _parse_choice_text(value: str) -> int | None:
    match = _CHOICE_RE.match(value or "")
    if not match:
        return None
    return _CHOICE_DIGITS.get(match.group(1))


def is_explicit_tower_faq_request(text: str) -> bool:
    value = (text or "").strip()
    if not value:
        return False
    if re.search(r"\bfaq\b", value, re.IGNORECASE):
        return True
    return bool(_FAQ_TERM_RE.search(value) and _FAQ_QUERY_VERB_RE.search(value))


def _log_result(source: SessionSource, result: TowerFaqQueryResult) -> None:
    top = result.top_match
    logger.info(
        "Tower FAQ preflight result: tenant=%s platform=%s chat_hash=%s user_hash=%s "
        "question_hash=%s mode=%s faq_id=%s score=%.4f",
        result.tenant_id,
        source.platform.value,
        _hash_text(source.chat_id or ""),
        _hash_text(source.user_id or ""),
        result.question_hash,
        result.answer_mode_hint or "unknown",
        top.faq_id if top else "",
        top.score if top else 0.0,
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _string_map(value: Any) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key).strip(): str(raw_value).strip()
        for key, raw_value in value.items()
        if str(key).strip() and str(raw_value).strip()
    }


def _first(payload: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def _string_env(*keys: str, fallback: str = "") -> str:
    for key in keys:
        value = os.getenv(key)
        if value is not None and value.strip():
            return value.strip()
    return fallback


def _string_value(value: Any) -> str:
    return str(value or "").strip()


def _coerce_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _coerce_int(*values: Any, default: int, minimum: int, maximum: int) -> int:
    for value in values:
        if value is None:
            continue
        try:
            return max(minimum, min(maximum, int(value)))
        except (TypeError, ValueError):
            continue
    return default


def _coerce_float(
    *values: Any,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    for value in values:
        if value is None:
            continue
        try:
            return max(minimum, min(maximum, float(value)))
        except (TypeError, ValueError):
            continue
    return default


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12] if value else ""
