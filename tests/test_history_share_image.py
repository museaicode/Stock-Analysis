# -*- coding: utf-8 -*-

import io
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image
from fastapi import HTTPException

from api.v1.endpoints import history as history_endpoint
from src.share_image import ShareImageBranding


class _FakeHistoryService:
    def __init__(self, result, markdown="# 中钨高新 000657 分析报告"):
        self.result = result
        self.markdown = markdown

    def resolve_and_get_detail(self, record_id):
        return self.result

    def get_markdown_report(self, record_id):
        return self.markdown


def _patch_service(monkeypatch, result, markdown="# 中钨高新 000657 分析报告"):
    service = _FakeHistoryService(result, markdown)
    monkeypatch.setattr(history_endpoint, "HistoryService", lambda _db: service)
    monkeypatch.setattr(
        history_endpoint,
        "get_config",
        lambda: SimpleNamespace(
            markdown_to_image_max_chars=15000,
            md2img_engine="markdown-to-file",
        ),
    )
    return service


def _report_like_png(width=1080, height=3000, seed=0) -> bytes:
    """生成带低幅噪点、浅色背景的长图，近似真实报告长图（大面积浅底 + 细节），PNG 体积较大。"""
    rng = np.random.default_rng(seed)
    array = (200 + rng.integers(0, 40, (height, width, 3))).astype(np.uint8)
    buffer = io.BytesIO()
    Image.fromarray(array).save(buffer, format="PNG")
    return buffer.getvalue()


def test_history_share_image_returns_compressed_jpeg(monkeypatch):
    raw_result = {"code": "000657", "name": "中钨高新", "dashboard": {}}
    _patch_service(
        monkeypatch,
        {
            "id": 17,
            "report_type": "detailed",
            "raw_result": raw_result,
            "context_snapshot": {},
        },
    )
    png_bytes = _report_like_png()
    monkeypatch.setattr(history_endpoint, "markdown_to_image", lambda *_a, **_k: png_bytes)

    response = history_endpoint.get_history_share_image("17", db_manager=object())

    assert response.status_code == 200
    assert response.media_type == "image/jpeg"
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["content-disposition"] == 'attachment; filename="dsa-report-17.jpg"'
    assert response.headers["cache-control"] == "no-store"
    assert response.body[:3] == b"\xff\xd8\xff"
    # 体积明显小于原始 PNG
    assert len(response.body) < len(png_bytes) / 2
    with Image.open(io.BytesIO(response.body)) as image:
        assert image.format == "JPEG"
        assert image.mode == "RGB"
        assert image.size == (1080, 3000)


def test_history_share_image_flattens_transparent_png_to_jpeg(monkeypatch):
    _patch_service(
        monkeypatch,
        {"id": 22, "report_type": "detailed", "raw_result": {}, "context_snapshot": {}},
    )
    buffer = io.BytesIO()
    Image.new("RGBA", (1080, 400), (0, 0, 0, 0)).save(buffer, format="PNG")
    monkeypatch.setattr(history_endpoint, "markdown_to_image", lambda *_a, **_k: buffer.getvalue())

    response = history_endpoint.get_history_share_image("22", db_manager=object())

    assert response.media_type == "image/jpeg"
    with Image.open(io.BytesIO(response.body)) as image:
        assert image.mode == "RGB"
        # 透明区域铺白底，而不是变黑
        assert image.getpixel((10, 10))[0] > 240


def test_history_share_image_keeps_png_when_jpeg_conversion_unavailable(monkeypatch):
    _patch_service(
        monkeypatch,
        {"id": 23, "report_type": "detailed", "raw_result": {}, "context_snapshot": {}},
    )
    png_bytes = _report_like_png(height=200)
    monkeypatch.setattr(history_endpoint, "markdown_to_image", lambda *_a, **_k: png_bytes)
    # 模拟高度超过 JPEG 单边上限等无法转 JPEG 的情况
    monkeypatch.setattr(history_endpoint, "compress_image_to_jpeg", lambda *_a, **_k: None)

    response = history_endpoint.get_history_share_image("23", db_manager=object())

    assert response.media_type == "image/png"
    assert response.body == png_bytes
    assert response.headers["content-disposition"] == 'attachment; filename="dsa-report-23.png"'


def test_history_share_image_falls_back_to_png_for_undecodable_bytes(monkeypatch):
    raw_result = {"code": "000657", "name": "中钨高新", "dashboard": {}}
    _patch_service(
        monkeypatch,
        {
            "id": 17,
            "report_type": "detailed",
            "raw_result": raw_result,
            "context_snapshot": {},
        },
    )
    calls = []

    def fake_markdown_to_image(markdown, **kwargs):
        calls.append((markdown, kwargs))
        return b"\x89PNG\r\n\x1a\nposter"

    monkeypatch.setattr(history_endpoint, "markdown_to_image", fake_markdown_to_image)

    response = history_endpoint.get_history_share_image("17", db_manager=object())

    assert response.status_code == 200
    assert response.media_type == "image/png"
    assert response.body.startswith(b"\x89PNG")
    assert response.headers["content-disposition"] == 'attachment; filename="dsa-report-17.png"'
    assert calls[0][1]["structured_payload"] is raw_result
    assert calls[0][1]["max_chars"] == 15000


def test_history_share_image_prefers_market_review_payload(monkeypatch):
    market_payload = {"kind": "market_review", "date": "2026-08-01"}
    _patch_service(
        monkeypatch,
        {
            "id": 18,
            "report_type": "market_review",
            "raw_result": {"raw_response": "market report"},
            "context_snapshot": {"market_review_payload": market_payload},
        },
        markdown="# A股市场复盘",
    )
    captured = {}

    def fake_markdown_to_image(markdown, **kwargs):
        captured.update(kwargs)
        return b"png"

    monkeypatch.setattr(history_endpoint, "markdown_to_image", fake_markdown_to_image)

    history_endpoint.get_history_share_image("18", db_manager=object())

    assert captured["structured_payload"] is market_payload


def test_history_share_image_html_returns_desktop_poster_with_restrictive_csp(monkeypatch):
    raw_result = {"code": "000657", "name": "中钨高新", "dashboard": {}}
    _patch_service(
        monkeypatch,
        {
            "id": 20,
            "report_type": "detailed",
            "raw_result": raw_result,
            "context_snapshot": {},
        },
    )
    captured = {}

    def fake_build_share_image_html(markdown, **kwargs):
        captured["markdown"] = markdown
        captured.update(kwargs)
        return "<!DOCTYPE html><html><body>poster</body></html>"

    monkeypatch.setattr(
        history_endpoint,
        "build_share_image_html",
        fake_build_share_image_html,
    )

    response = history_endpoint.get_history_share_image_html("20", db_manager=object())

    assert response.status_code == 200
    assert response.media_type == "text/html"
    assert b"poster" in response.body
    assert captured["structured_payload"] is raw_result
    assert captured["branding"] == ShareImageBranding()
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-security-policy"] == (
        "default-src 'none'; img-src data:; style-src 'unsafe-inline'"
    )


def test_history_share_image_html_default_poster_has_no_upstream_branding(monkeypatch):
    _patch_service(
        monkeypatch,
        {
            "id": 22,
            "report_type": "detailed",
            "raw_result": {"code": "000657", "name": "中钨高新", "dashboard": {}},
            "context_snapshot": {},
        },
    )

    response = history_endpoint.get_history_share_image_html("22", db_manager=object())
    body = response.body.decode("utf-8")

    assert response.status_code == 200
    assert "ZhuLinsen/daily_stock_analysis" not in body
    assert "霸天土小豆" not in body
    assert "data:image" not in body
    assert 'class="repo-line"' not in body
    assert 'class="qr-' not in body
    assert "仅供研究交流，不构成投资建议" in body


def test_history_share_image_html_renders_configured_repo_and_handle(monkeypatch):
    _patch_service(
        monkeypatch,
        {
            "id": 23,
            "report_type": "detailed",
            "raw_result": {"code": "000657", "name": "中钨高新", "dashboard": {}},
            "context_snapshot": {},
        },
    )
    monkeypatch.setattr(
        history_endpoint,
        "get_config",
        lambda: SimpleNamespace(
            markdown_to_image_max_chars=15000,
            share_image_repo_url="https://github.com/example/my-fork",
            share_image_xiaohongshu_handle="@我的账号",
        ),
    )

    body = history_endpoint.get_history_share_image_html("23", db_manager=object()).body.decode("utf-8")

    assert "github.com/example/my-fork" in body
    assert "<b>小红书</b>@我的账号" in body
    assert 'class="qr-frame"' not in body


def test_history_share_image_html_rejects_reports_over_configured_limit(monkeypatch):
    _patch_service(
        monkeypatch,
        {
            "id": 21,
            "report_type": "detailed",
            "raw_result": {"code": "000657"},
            "context_snapshot": {},
        },
        markdown="x" * 15001,
    )

    with pytest.raises(HTTPException) as exc_info:
        history_endpoint.get_history_share_image_html("21", db_manager=object())

    assert exc_info.value.status_code == 413
    assert exc_info.value.detail["error"] == "share_image_too_large"


def test_history_share_image_reports_renderer_unavailable(monkeypatch):
    _patch_service(
        monkeypatch,
        {
            "id": 19,
            "report_type": "detailed",
            "raw_result": {"code": "000657"},
            "context_snapshot": {},
        },
    )
    monkeypatch.setattr(history_endpoint, "markdown_to_image", lambda *_args, **_kwargs: None)

    with pytest.raises(HTTPException) as exc_info:
        history_endpoint.get_history_share_image("19", db_manager=object())

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["error"] == "share_image_unavailable"


def test_history_share_image_returns_not_found(monkeypatch):
    _patch_service(monkeypatch, None)

    with pytest.raises(HTTPException) as exc_info:
        history_endpoint.get_history_share_image("missing", db_manager=object())

    assert exc_info.value.status_code == 404


def test_compress_image_to_jpeg_returns_none_when_not_convertible():
    from src.utils import image_compress

    png_bytes = _report_like_png(height=300)
    with Image.open(io.BytesIO(png_bytes)) as image:
        assert image_compress.encode_jpeg(image.convert("RGB"), 85, max_dimension=200) is None
    assert image_compress.compress_image_to_jpeg(b"") is None
    assert image_compress.compress_image_to_jpeg(b"not an image") is None
