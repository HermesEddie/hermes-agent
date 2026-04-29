"""Tower RAG knowledge-base query integration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import logging
import os
from typing import Any


logger = logging.getLogger(__name__)


_DEFAULT_QUERY_PATH = "/api/internal/agent-workspace/knowledge-base/query"
_ALLOWED_MODES = {"keyword", "vector", "hybrid"}


@dataclass(frozen=True)
class TowerRagSettings:
    enabled: bool = False
    query_url: str = ""
    token: str = ""
    default_tenant_id: str = ""
    default_agent_id: str = "tower_system"
    retrieval_mode: str = "hybrid"
    limit: int = 5
    timeout_seconds: float = 8.0
    max_question_chars: int = 2000
    tenant_by_chat: Mapping[str, str] = field(default_factory=dict)
    tenant_by_user: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_config(cls, config: Mapping[str, Any] | None) -> "TowerRagSettings":
        config = config or {}
        tower_cfg = _mapping(config.get("tower"))
        rag_cfg = _mapping(tower_cfg.get("rag") or tower_cfg.get("knowledge_base") or tower_cfg.get("knowledgeBase"))

        enabled = _coerce_bool(
            os.getenv("TOWER_RAG_ENABLED"),
            default=_coerce_bool(rag_cfg.get("enabled"), default=False),
        )
        base_url = _string_env(
            "TOWER_BASE_URL",
            "TOWER_API_BASE_URL",
            fallback=_string_value(tower_cfg.get("base_url") or tower_cfg.get("baseUrl")),
        )
        query_url = _string_env(
            "TOWER_RAG_QUERY_URL",
            "TOWER_KNOWLEDGE_QUERY_URL",
            fallback=_string_value(rag_cfg.get("query_url") or rag_cfg.get("queryUrl")),
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
                rag_cfg.get("tenant_id")
                or rag_cfg.get("tenantId")
                or tower_cfg.get("tenant_id")
                or tower_cfg.get("tenantId")
            ),
        )
        default_agent_id = _string_env(
            "TOWER_RAG_AGENT_ID",
            "TOWER_AGENT_ID",
            fallback=_string_value(rag_cfg.get("agent_id") or rag_cfg.get("agentId") or "tower_system"),
        )
        retrieval_mode = _string_env(
            "TOWER_RAG_RETRIEVAL_MODE",
            fallback=_string_value(rag_cfg.get("retrieval_mode") or rag_cfg.get("retrievalMode") or "hybrid"),
        )
        if retrieval_mode not in _ALLOWED_MODES:
            retrieval_mode = "hybrid"

        return cls(
            enabled=enabled,
            query_url=query_url,
            token=token,
            default_tenant_id=default_tenant_id,
            default_agent_id=default_agent_id or "tower_system",
            retrieval_mode=retrieval_mode,
            limit=_coerce_int(os.getenv("TOWER_RAG_LIMIT"), rag_cfg.get("limit"), default=5, minimum=1, maximum=20),
            timeout_seconds=_coerce_float(
                os.getenv("TOWER_RAG_TIMEOUT_SECONDS"),
                rag_cfg.get("timeout_seconds") or rag_cfg.get("timeoutSeconds"),
                default=8.0,
                minimum=0.5,
                maximum=60.0,
            ),
            max_question_chars=_coerce_int(
                os.getenv("TOWER_RAG_MAX_QUESTION_CHARS"),
                rag_cfg.get("max_question_chars") or rag_cfg.get("maxQuestionChars"),
                default=2000,
                minimum=20,
                maximum=8000,
            ),
            tenant_by_chat=_string_map(rag_cfg.get("tenant_by_chat") or rag_cfg.get("tenantByChat")),
            tenant_by_user=_string_map(rag_cfg.get("tenant_by_user") or rag_cfg.get("tenantByUser")),
        )

    def is_configured(self) -> bool:
        return bool(self.enabled and self.query_url and self.token)


@dataclass(frozen=True)
class TowerRagQueryResult:
    tenant_id: str
    question_hash: str
    matches: tuple[dict[str, Any], ...] = ()

    @classmethod
    def from_payload(
        cls,
        *,
        tenant_id: str,
        question: str,
        payload: Mapping[str, Any],
    ) -> "TowerRagQueryResult":
        raw_matches = _first(payload, "matches") or []
        matches = tuple(dict(item) for item in raw_matches if isinstance(item, Mapping))
        return cls(
            tenant_id=tenant_id,
            question_hash=_hash_text(question),
            matches=matches,
        )

    def citations(self) -> list[dict[str, Any]]:
        citations: list[dict[str, Any]] = []
        for match in self.matches:
            source = _mapping(match.get("source"))
            citations.append(
                {
                    "chunk_id": _string_value(_first(match, "chunkId", "chunk_id")),
                    "document_id": _string_value(_first(match, "documentId", "document_id")),
                    "document_name": _string_value(_first(source, "documentName", "document_name")),
                    "file_type": _string_value(_first(source, "fileType", "file_type")),
                    "module": _string_value(source.get("module")),
                    "page_no": _first(source, "pageNo", "page_no"),
                    "section_path": _string_value(_first(source, "sectionPath", "section_path")),
                    "score": _coerce_float(_first(match, "score"), default=0.0, minimum=0.0, maximum=1.0),
                }
            )
        return citations

    def response_context(self) -> str:
        lines: list[str] = []
        for index, match in enumerate(self.matches, start=1):
            source = _mapping(match.get("source"))
            title = _string_value(match.get("title")) or _string_value(_first(source, "sectionPath", "section_path"))
            document_name = _string_value(_first(source, "documentName", "document_name"))
            page_no = _first(source, "pageNo", "page_no")
            location = f"第 {page_no} 页" if page_no else "页码未知"
            content = _string_value(match.get("content"))
            lines.append(f"[{index}] {document_name} - {location} - {title}\n{content}")
        return "\n\n".join(lines)


class TowerRagClient:
    def __init__(self, settings: TowerRagSettings):
        self.settings = settings

    @classmethod
    def from_config(cls, config: Mapping[str, Any] | None) -> "TowerRagClient":
        return cls(TowerRagSettings.from_config(config))

    async def query_question(
        self,
        question: str,
        *,
        tenant_id: str = "",
        agent_id: str = "",
        user_id: str = "",
        user_roles: list[str] | None = None,
        module: str | None = None,
    ) -> TowerRagQueryResult | None:
        question = (question or "").strip()
        tenant_id = (tenant_id or self.settings.default_tenant_id).strip()
        agent_id = (agent_id or self.settings.default_agent_id).strip()
        if not question or len(question) > self.settings.max_question_chars:
            return None
        if not self.settings.is_configured() or not tenant_id:
            return None

        body = {
            "tenantId": tenant_id,
            "question": question,
            "agentId": agent_id,
            "userId": user_id,
            "userRoles": user_roles or [],
            "module": module,
            "limit": self.settings.limit,
            "retrievalMode": self.settings.retrieval_mode,
        }
        try:
            payload = await self._post_json(body)
        except Exception as exc:
            logger.warning("Tower RAG query failed: %s", exc)
            return None
        result = TowerRagQueryResult.from_payload(
            tenant_id=tenant_id,
            question=question,
            payload=payload,
        )
        logger.info(
            "Tower RAG tool result: tenant=%s question_hash=%s matches=%d",
            result.tenant_id,
            result.question_hash,
            len(result.matches),
        )
        return result

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
                    raise RuntimeError(f"Tower RAG query HTTP {response.status}: {text[:300]}")
                data = await response.json()
                if not isinstance(data, Mapping):
                    raise RuntimeError("Tower RAG query returned non-object JSON")
                return data


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first(payload: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def _string_map(value: Any) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key).strip(): str(raw_value).strip()
        for key, raw_value in value.items()
        if str(key).strip() and str(raw_value).strip()
    }


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
