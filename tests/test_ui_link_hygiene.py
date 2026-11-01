# -*- coding: utf-8 -*-
"""Links surfaced in the Web settings UI must be plain and point at this repo."""

from __future__ import annotations

import json
import re
from pathlib import Path

from src.core.config_registry import build_schema_response

REPO_ROOT = Path(__file__).resolve().parents[1]
_TRACKING_PARAM = re.compile(r"[?&](aff|share_code|ref|utm_[a-z]+)=", re.IGNORECASE)
_WEB_SETTINGS_SOURCES = [
    REPO_ROOT / "apps/dsa-web/src/components/settings/llmProviderTemplates.ts",
    REPO_ROOT / "apps/dsa-web/src/components/settings/LLMChannelEditor.tsx",
]


def test_config_registry_has_no_referral_or_tracking_links() -> None:
    payload = json.dumps(build_schema_response(), ensure_ascii=False)

    assert not _TRACKING_PARAM.search(payload), _TRACKING_PARAM.search(payload)
    assert "https://aihubmix.com" in payload


def test_web_settings_sources_have_no_referral_or_tracking_links() -> None:
    for path in _WEB_SETTINGS_SOURCES:
        text = path.read_text(encoding="utf-8")
        assert not _TRACKING_PARAM.search(text), (path, _TRACKING_PARAM.search(text))
