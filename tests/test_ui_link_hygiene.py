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


def test_settings_help_links_use_this_repository_docs() -> None:
    from src.core import config_registry

    assert config_registry.DOCS_BASE_URL == "https://github.com/museaicode/Stock-Analysis/blob/main/docs/"
    payload = json.dumps(build_schema_response(), ensure_ascii=False)
    assert "ZhuLinsen" not in payload
    hrefs = [
        doc["href"]
        for field in build_schema_response()["categories"]
        for item in field.get("fields", [])
        for doc in item.get("docs", [])
    ]
    assert hrefs
    for href in hrefs:
        if "/blob/main/docs/" in href:
            assert href.startswith(config_registry.DOCS_BASE_URL), href


def test_registry_and_web_sources_do_not_hardcode_upstream_doc_links() -> None:
    upstream_docs = "github.com/ZhuLinsen/daily_stock_analysis/blob/main/docs/"
    sources = [REPO_ROOT / "src/core/config_registry.py"] + sorted(
        path
        for path in (REPO_ROOT / "apps/dsa-web/src").rglob("*")
        if path.suffix in {".ts", ".tsx"} and "__tests__" not in path.parts
    )
    offenders = [str(path) for path in sources if upstream_docs in path.read_text(encoding="utf-8")]
    assert offenders == []

    py_base = re.search(
        r'^DOCS_BASE_URL = "([^"]+)"$',
        (REPO_ROOT / "src/core/config_registry.py").read_text(encoding="utf-8"),
        re.M,
    )
    ts_base = re.search(
        r"^export const DOCS_BASE_URL = '([^']+)';$",
        (REPO_ROOT / "apps/dsa-web/src/utils/constants.ts").read_text(encoding="utf-8"),
        re.M,
    )
    assert py_base and ts_base
    assert py_base.group(1) == ts_base.group(1)
