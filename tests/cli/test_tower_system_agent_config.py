"""Tests for Tower System Agent persona and skill packaging."""

from __future__ import annotations

from pathlib import Path


def test_default_config_exposes_tower_system_personality():
    from cli import load_cli_config

    config = load_cli_config()
    personality = config["agent"]["personalities"]["tower_system"]

    assert "Tower System Agent" in personality["system_prompt"]
    assert "tower_faq_query" in personality["system_prompt"]
    assert "tower_target_approval_review" in personality["system_prompt"]


def test_tower_system_skill_defines_routing_contract():
    skill_path = Path("skills/business/tower-system-agent/SKILL.md")

    assert skill_path.exists()
    text = skill_path.read_text(encoding="utf-8")
    assert "tower_faq_query" in text
    assert "tower_target_approval_review" in text
    assert "不确定" in text
