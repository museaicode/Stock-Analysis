# -*- coding: utf-8 -*-
"""Web / 桌面分享图 ``layout=card|full`` 请求参数回归测试。

覆盖：
- ``layout=full`` 返回完整报告长图（关键章节都在渲染内容中），输出 JPEG、文件名带 ``_full``；
- ``layout=card`` 与不传参数一致（决策卡），文件名带 ``_card``；
- 请求参数与推送图配置 ``MARKDOWN_TO_IMAGE_LAYOUT`` 相互独立；
- 非法值返回 400；
- 服务端不缓存分享图（``Cache-Control: no-store``，每次按请求版式重新渲染），两种版式不会互相串用。
"""

import os
import sys
from unittest import mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tests.test_markdown_to_image_layout import (  # noqa: E402  复用完整报告样例与渲染替身
    STOCK_SECTION_TITLES,
    _assert_full_sections,
    _layout_config,
    _patch_history,
    _render_capture,
)
from tests.test_history_share_image import _report_like_png  # noqa: E402
from api.deps import get_database_manager  # noqa: E402
from api.v1.endpoints import history as history_endpoint  # noqa: E402
from src import md2img  # noqa: E402


def _render_share_image(monkeypatch, layout_arg=None, *, config_layout="card"):
    """调用 share-image 接口；真实生成 HTML，仅替换外部转图工具。"""

    _patch_history(monkeypatch, config_layout)
    captured = []
    kwargs = {} if layout_arg is None else {"layout": layout_arg}
    with mock.patch("src.config.get_config", return_value=_layout_config(config_layout)), \
            mock.patch.object(md2img, "_markdown_to_image_wkhtml", side_effect=_render_capture(captured)):
        response = history_endpoint.get_history_share_image("30", db_manager=object(), **kwargs)
    return response, captured


def test_share_image_full_layout_renders_complete_report(monkeypatch):
    response, captured = _render_share_image(monkeypatch, "full")

    assert response.status_code == 200
    assert captured[0]["layout"] == "full"
    html = captured[0]["html"]
    _assert_full_sections(html, STOCK_SECTION_TITLES)
    assert "完整分析报告" in html
    assert 'class="signal-row"' not in html
    # 沿用 JPEG 压缩，Content-Type / 扩展名与实际格式一致，文件名区分版式
    assert response.media_type == "image/jpeg"
    assert response.body[:3] == b"\xff\xd8\xff"
    assert response.headers["content-disposition"] == 'attachment; filename="dsa-report-30_full.jpg"'


def test_share_image_card_layout_matches_default(monkeypatch):
    default_response, default_captured = _render_share_image(monkeypatch)
    card_response, card_captured = _render_share_image(monkeypatch, "card")

    assert default_captured[0]["layout"] == "card"
    assert card_captured[0]["html"] == default_captured[0]["html"]
    assert 'class="signal-row"' in card_captured[0]["html"]
    assert "检查清单" not in card_captured[0]["html"]
    assert card_response.body == default_response.body
    assert card_response.headers["content-disposition"] == 'attachment; filename="dsa-report-30_card.jpg"'
    assert default_response.headers["content-disposition"] == 'attachment; filename="dsa-report-30_card.jpg"'


@pytest.mark.parametrize(
    ("config_layout", "layout_arg", "expected"),
    [("full", None, "card"), ("full", "card", "card"), ("card", "full", "full")],
)
def test_share_image_layout_is_independent_of_push_config(monkeypatch, config_layout, layout_arg, expected):
    _, captured = _render_share_image(monkeypatch, layout_arg, config_layout=config_layout)

    assert captured[0]["layout"] == expected


def test_share_image_layout_is_case_insensitive(monkeypatch):
    _, captured = _render_share_image(monkeypatch, " FULL ")

    assert captured[0]["layout"] == "full"


def test_share_image_png_fallback_keeps_layout_in_filename(monkeypatch):
    _patch_history(monkeypatch, "card")
    monkeypatch.setattr(history_endpoint, "markdown_to_image", lambda *_a, **_k: _report_like_png(height=200))
    monkeypatch.setattr(history_endpoint, "compress_image_to_jpeg", lambda *_a, **_k: None)

    response = history_endpoint.get_history_share_image("30", layout="full", db_manager=object())

    assert response.media_type == "image/png"
    assert response.headers["content-disposition"] == 'attachment; filename="dsa-report-30_full.png"'


def test_share_image_layouts_are_rendered_per_request_without_server_cache(monkeypatch):
    """服务端无分享图缓存：交替请求两种版式，每次都按本次版式渲染，不会串用。"""

    _patch_history(monkeypatch, "card")
    calls = []

    def fake_markdown_to_image(markdown, **kwargs):
        calls.append(kwargs["layout"])
        return _report_like_png(height=120 if kwargs["layout"] == "card" else 360)

    monkeypatch.setattr(history_endpoint, "markdown_to_image", fake_markdown_to_image)

    responses = [
        history_endpoint.get_history_share_image("30", layout=layout, db_manager=object())
        for layout in ("full", "card", "full", "card")
    ]

    assert calls == ["full", "card", "full", "card"]
    assert responses[0].body == responses[2].body
    assert responses[1].body == responses[3].body
    assert responses[0].body != responses[1].body
    for response in responses:
        assert response.headers["cache-control"] == "no-store"


def test_share_image_html_full_layout_renders_complete_report(monkeypatch):
    _patch_history(monkeypatch, "card")

    full_body = history_endpoint.get_history_share_image_html(
        "30", layout="full", db_manager=object()
    ).body.decode("utf-8")
    card_body = history_endpoint.get_history_share_image_html(
        "30", layout="card", db_manager=object()
    ).body.decode("utf-8")
    default_body = history_endpoint.get_history_share_image_html("30", db_manager=object()).body.decode("utf-8")

    _assert_full_sections(full_body, STOCK_SECTION_TITLES)
    assert 'class="signal-row"' not in full_body
    assert card_body == default_body
    assert 'class="signal-row"' in card_body


@pytest.mark.parametrize("handler", ["get_history_share_image", "get_history_share_image_html"])
def test_share_image_rejects_invalid_layout(monkeypatch, handler):
    _patch_history(monkeypatch, "card")

    with pytest.raises(HTTPException) as exc_info:
        getattr(history_endpoint, handler)("30", layout="poster", db_manager=object())

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"] == "invalid_layout"


@pytest.fixture()
def client(monkeypatch):
    _patch_history(monkeypatch, "card")
    app = FastAPI()
    app.include_router(history_endpoint.router, prefix="/api/v1/history")
    app.dependency_overrides[get_database_manager] = lambda: object()
    return TestClient(app)


@pytest.mark.parametrize("path", ["share-image", "share-image-html"])
def test_share_image_http_query_validation(client, monkeypatch, path):
    monkeypatch.setattr(history_endpoint, "markdown_to_image", lambda *_a, **_k: _report_like_png(height=120))

    invalid = client.get(f"/api/v1/history/30/{path}", params={"layout": "poster"})
    assert invalid.status_code == 400
    assert invalid.json()["detail"]["error"] == "invalid_layout"

    for params in ({}, {"layout": "card"}, {"layout": "full"}):
        assert client.get(f"/api/v1/history/30/{path}", params=params).status_code == 200


def test_share_image_http_layout_query_reaches_renderer(client, monkeypatch):
    seen = []

    def fake_markdown_to_image(markdown, **kwargs):
        seen.append(kwargs["layout"])
        return _report_like_png(height=120)

    monkeypatch.setattr(history_endpoint, "markdown_to_image", fake_markdown_to_image)

    full = client.get("/api/v1/history/30/share-image", params={"layout": "full"})
    default = client.get("/api/v1/history/30/share-image")

    assert seen == ["full", "card"]
    assert full.headers["content-type"] == "image/jpeg"
    assert 'filename="dsa-report-30_full.jpg"' in full.headers["content-disposition"]
    assert 'filename="dsa-report-30_card.jpg"' in default.headers["content-disposition"]
    full_html = client.get("/api/v1/history/30/share-image-html", params={"layout": "full"}).text
    _assert_full_sections(full_html, STOCK_SECTION_TITLES)
