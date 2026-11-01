# -*- coding: utf-8 -*-
"""MARKDOWN_TO_IMAGE_LAYOUT（推送图版式 card / full）回归测试。

覆盖：
- full 版式渲染完整报告各章节；card 版式与原有行为一致；
- 单股、多股汇总、大盘复盘三种推送在 full 模式下使用完整正文；
- Web 分享按钮（历史报告分享图）不受该配置影响；
- full 版式超长图经企业微信压缩/切图后每张不超过 2MB。
"""

import io
import os
import sys
from types import SimpleNamespace
from unittest import mock

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

from api.v1.endpoints import history as history_endpoint
from src import md2img
from src.config import Config
from src.core.pipeline import NotificationChannel, StockAnalysisPipeline
from src.enums import ReportType
from src.notification import NotificationService
from src.notification_sender.wechat_sender import (
    WECHAT_IMAGE_MAX_BYTES,
    WECHAT_IMAGE_MAX_SEGMENTS,
    _prepare_wechat_images,
)
from src.share_image import (
    PROJECT_BRAND_NAME,
    ShareImageBranding,
    build_share_image_html,
    normalize_share_image_layout,
)


def _stock_section(name: str, code: str) -> str:
    return f"""## 🟢 {name} ({code})

### 📰 重要信息速览

**💭 舆情情绪**: 偏乐观

**🚨 风险警报**:
- 估值偏高

**✨ 利好催化**:
- 提价预期

### 📌 核心结论

**🟢 买入** | 看多

> **一句话决策**: 多头趋势延续，回踩不破 MA10 可继续持有

| 持仓情况 | 操作建议 |
|---------|---------|
| 🆕 **空仓者** | 回踩 1680 附近分批建仓 |
| 💼 **持仓者** | 继续持有，跌破 1620 减仓 |

### 📊 数据透视

**均线排列**: 多头排列 | 趋势强度: 78/100

| 价格指标 | 当前价 |
|---------|------|
| 当前价 | 1700 |
| MA5 | 1690 |
| 支撑位 | 1650 |
| 压力位 | 1760 |

### 🎯 作战计划

| 操作点位 | 当前价 |
|---------|------|
| 🎯 理想买入点 | 1680 |
| 🛑 止损位 | 1620 |
| 🎊 目标位 | 1800 |

**💰 仓位建议**: 3成

**✅ 检查清单**

- ✅ 多头排列
- ⚠️ 量能一般

---
"""


# NotificationService.generate_dashboard_report 的完整报告结构（REPORT_TYPE=full）。
SINGLE_FULL_REPORT = f"""# 🎯 2026-11-01 决策仪表盘

> 共分析 **1** 只股票 | 🟢买入:1 🟡观望:0 🔴卖出:0

## 📊 分析结果摘要

🟢 **贵州茅台(600519)**: 买入 | 评分 72 | 看多

---

{_stock_section("贵州茅台", "600519")}

*报告生成时间：2026-11-01 13:30:24*
"""

MULTI_FULL_REPORT = f"""# 🎯 2026-11-01 决策仪表盘

> 共分析 **2** 只股票 | 🟢买入:2 🟡观望:0 🔴卖出:0

## 📊 分析结果摘要

🟢 **贵州茅台(600519)**: 买入 | 评分 72 | 看多
🟢 **五粮液(000858)**: 买入 | 评分 70 | 看多

---

{_stock_section("贵州茅台", "600519")}
{_stock_section("五粮液", "000858")}
"""

STOCK_SECTION_TITLES = ("重要信息速览", "核心结论", "数据透视", "作战计划", "检查清单")

MARKET_FULL_REPORT = """# 🎯 大盘复盘

## 2026-11-01 大盘复盘

> 今日A股温和上行，但仍需观察成交额能否延续。

### 一、盘面总览

- **盘面信号**：66/100（偏暖，可进攻）
- **操作建议**：关注主线延续，避免追高。

| 指标 | 数值 | 观察 |
| --- | --- | --- |
| 上涨/下跌/平盘 | 3298 / 1687 / 70 | 扩散 |
| 涨停/跌停 | 72 / 6 | 正向 |
| 两市成交额 | 1.12 万亿 | 活跃 |

### 二、指数结构

| 指数 | 最新 | 涨跌幅 |
| --- | --- | --- |
| 上证指数 | 3185.53 | +0.80% |
| 深证成指 | 9732.28 | +1.12% |

### 三、板块主线

| 排名 | 板块 | 涨跌幅 |
| --- | --- | --- |
| 1 | 半导体 | +3.2% |
| 2 | 消费电子 | +2.6% |

### 五、消息催化

- 国产算力产业链活跃。

### 六、明日交易计划

**结论：建设性偏多。**
**仓位区间：** 5-7成。

### 七、风险提示

1. 高位板块回撤。
"""

MARKET_SECTION_TITLES = ("盘面总览", "指数结构", "板块主线", "消息催化", "明日交易计划", "风险提示")

STOCK_PAYLOAD = {
    "code": "600519",
    "name": "贵州茅台",
    "sentiment_score": 72,
    "trend_prediction": "看多",
    "operation_advice": "买入",
    "dashboard": {
        "core_conclusion": {"one_sentence": "多头趋势延续，回踩不破 MA10 可继续持有"},
        "battle_plan": {
            "sniper_points": {"ideal_buy": "1680", "stop_loss": "1620", "take_profit": "1800"},
        },
    },
}


def _assert_full_sections(html: str, titles) -> None:
    for title in titles:
        assert title in html, f"full 版式缺少章节：{title}"
    assert 'class="report-content"' in html
    # 保留品牌页眉与底部风险提示
    assert "poster-header" in html
    assert PROJECT_BRAND_NAME in html
    assert 'class="disclaimer"' in html


def _small_png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (1080, 400), (240, 244, 250)).save(buffer, format="PNG")
    return buffer.getvalue()


def _render_capture(captured: list):
    """替换转图引擎：真实生成 HTML 并记录，不调用外部工具。"""

    def _fake_engine(markdown_text, structured_payload=None, branding=None, layout="card"):
        html = build_share_image_html(
            markdown_text,
            structured_payload=structured_payload,
            branding=branding,
            layout=layout,
        )
        captured.append({"markdown": markdown_text, "layout": layout, "html": html})
        return _small_png()

    return _fake_engine


def _layout_config(layout: str) -> SimpleNamespace:
    return SimpleNamespace(
        md2img_engine="wkhtmltoimage",
        markdown_to_image_layout=layout,
        markdown_to_image_max_chars=15000,
    )


# ---------------------------------------------------------------------------
# HTML 模板
# ---------------------------------------------------------------------------


def test_full_layout_renders_every_single_stock_report_section():
    html = build_share_image_html(SINGLE_FULL_REPORT, structured_payload=STOCK_PAYLOAD, layout="full")

    _assert_full_sections(html, STOCK_SECTION_TITLES)
    assert "分析结果摘要" in html
    assert "回踩 1680 附近分批建仓" in html
    assert "报告生成时间" in html
    assert "完整分析报告" in html
    assert "个股决策卡" not in html
    # 不再输出决策卡模块
    assert 'class="signal-row"' not in html


def test_card_layout_matches_default_decision_card():
    default_html = build_share_image_html(SINGLE_FULL_REPORT, structured_payload=STOCK_PAYLOAD)
    card_html = build_share_image_html(SINGLE_FULL_REPORT, structured_payload=STOCK_PAYLOAD, layout="card")

    assert card_html == default_html
    assert 'class="signal-row"' in card_html
    # 决策卡不含完整正文章节
    assert "重要信息速览" not in card_html
    assert "检查清单" not in card_html


def test_full_layout_renders_multi_stock_dashboard_completely():
    html = build_share_image_html(MULTI_FULL_REPORT, layout="full")

    _assert_full_sections(html, STOCK_SECTION_TITLES)
    assert "贵州茅台" in html and "五粮液" in html
    assert html.count("作战计划") == 2


def test_full_layout_renders_every_market_review_section():
    card_html = build_share_image_html(MARKET_FULL_REPORT)
    html = build_share_image_html(MARKET_FULL_REPORT, layout="full")

    _assert_full_sections(html, MARKET_SECTION_TITLES)
    assert 'class="poster market"' in html
    assert 'class="market-signal"' not in html
    # card 版式仍是结构化决策卡，且不含完整正文中的章节标题
    assert '<section class="market-signal">' in card_html
    for title in ("盘面总览", "指数结构", "明日交易计划"):
        assert title not in card_html


def test_full_layout_keeps_configured_footer_branding():
    branding = ShareImageBranding(repo_url="https://example.com/miusiai", xiaohongshu_handle="@miusi")
    html = build_share_image_html(SINGLE_FULL_REPORT, branding=branding, layout="full")

    assert "example.com/miusiai" in html
    assert "@miusi" in html


@pytest.mark.parametrize("value", [None, "", "CARD", " card ", "poster", "Full "])
def test_layout_normalization(value):
    expected = "full" if str(value or "").strip().lower() == "full" else "card"
    assert normalize_share_image_layout(value) == expected


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, "card"), ("", "card"), ("full", "full"), ("FULL", "full"), ("card", "card"), ("long", "card")],
)
def test_config_parses_markdown_to_image_layout(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("MARKDOWN_TO_IMAGE_LAYOUT", raising=False)
        assert Config._parse_markdown_to_image_layout(os.getenv("MARKDOWN_TO_IMAGE_LAYOUT", "card")) == expected
    else:
        assert Config._parse_markdown_to_image_layout(raw) == expected


def test_config_default_layout_is_card():
    assert Config(stock_list=[]).markdown_to_image_layout == "card"


# ---------------------------------------------------------------------------
# markdown_to_image
# ---------------------------------------------------------------------------


def test_markdown_to_image_reads_layout_from_config():
    captured = []
    with mock.patch("src.config.get_config", return_value=_layout_config("full")), \
            mock.patch.object(md2img, "_markdown_to_image_wkhtml", side_effect=_render_capture(captured)):
        assert md2img.markdown_to_image(SINGLE_FULL_REPORT, structured_payload=STOCK_PAYLOAD)

    assert captured[0]["layout"] == "full"
    _assert_full_sections(captured[0]["html"], STOCK_SECTION_TITLES)


def test_markdown_to_image_explicit_layout_overrides_config():
    captured = []
    with mock.patch("src.config.get_config", return_value=_layout_config("full")), \
            mock.patch.object(md2img, "_markdown_to_image_wkhtml", side_effect=_render_capture(captured)):
        md2img.markdown_to_image(SINGLE_FULL_REPORT, structured_payload=STOCK_PAYLOAD, layout="card")

    assert captured[0]["layout"] == "card"
    assert 'class="signal-row"' in captured[0]["html"]


def test_full_layout_over_max_chars_keeps_existing_text_fallback():
    with mock.patch("src.config.get_config", return_value=_layout_config("full")), \
            mock.patch.object(md2img, "_markdown_to_image_wkhtml") as mock_engine:
        assert md2img.markdown_to_image(SINGLE_FULL_REPORT, max_chars=100) is None
    mock_engine.assert_not_called()


# ---------------------------------------------------------------------------
# 推送路径：单股 / 多股汇总 / 大盘复盘
# ---------------------------------------------------------------------------


def _wechat_service(layout: str) -> NotificationService:
    cfg = Config(
        stock_list=[],
        wechat_webhook_url="https://wechat.example/hook",
        markdown_to_image_channels=["wechat"],
        markdown_to_image_layout=layout,
    )
    with mock.patch("src.notification.get_config", return_value=cfg):
        return NotificationService()


@pytest.mark.parametrize(
    ("content", "payload", "titles"),
    [
        (SINGLE_FULL_REPORT, STOCK_PAYLOAD, STOCK_SECTION_TITLES),
        (MARKET_FULL_REPORT, None, MARKET_SECTION_TITLES),
    ],
    ids=["single-stock", "market-review"],
)
def test_wechat_push_uses_full_report_body_in_full_layout(content, payload, titles):
    service = _wechat_service("full")
    captured = []
    with mock.patch("src.config.get_config", return_value=_layout_config("full")), \
            mock.patch.object(md2img, "_markdown_to_image_wkhtml", side_effect=_render_capture(captured)), \
            mock.patch.object(service, "_send_wechat_report_image", return_value=True) as mock_image, \
            mock.patch.object(service, "send_to_wechat", return_value=True) as mock_text:
        assert service.send(content, route_type="report", structured_payload=payload)

    assert len(captured) == 1
    assert captured[0]["markdown"] == content
    _assert_full_sections(captured[0]["html"], titles)
    mock_image.assert_called_once()
    mock_text.assert_not_called()


def test_wechat_push_keeps_decision_card_in_card_layout():
    service = _wechat_service("card")
    captured = []
    with mock.patch("src.config.get_config", return_value=_layout_config("card")), \
            mock.patch.object(md2img, "_markdown_to_image_wkhtml", side_effect=_render_capture(captured)), \
            mock.patch.object(service, "_send_wechat_report_image", return_value=True):
        service.send(SINGLE_FULL_REPORT, route_type="report", structured_payload=STOCK_PAYLOAD)

    assert captured[0]["layout"] == "card"
    assert captured[0]["html"] == build_share_image_html(
        SINGLE_FULL_REPORT,
        structured_payload=STOCK_PAYLOAD,
        branding=ShareImageBranding(),
    )


class _AggregateWechatNotifier:
    def __init__(self):
        self._markdown_to_image_channels = {"wechat"}
        self._markdown_to_image_max_chars = 15000
        self.generate_dashboard_report = mock.MagicMock(return_value=MULTI_FULL_REPORT)
        self.generate_wechat_dashboard = mock.MagicMock(return_value="# 精简仪表盘\n\n- 贵州茅台: 买入")
        self.generate_brief_report = mock.MagicMock(return_value="brief")
        self.save_report_to_file = mock.MagicMock(return_value="/tmp/report.md")
        self.is_available = mock.MagicMock(return_value=True)
        self.get_available_channels = mock.MagicMock(return_value=[NotificationChannel.WECHAT])
        self.get_channels_for_route = mock.MagicMock(
            side_effect=lambda route_type, channels=None: list(
                channels if channels is not None else self.get_available_channels()
            )
        )
        self.send_to_context = mock.MagicMock(return_value=False)
        self._should_use_image_for_channel = mock.MagicMock(
            side_effect=lambda channel, image_bytes: image_bytes is not None
        )
        self._send_wechat_report_image = mock.MagicMock(return_value=True)
        self.send_to_wechat = mock.MagicMock(return_value=True)


@pytest.mark.parametrize(
    ("layout", "expected_source"),
    [("full", MULTI_FULL_REPORT), ("card", "# 精简仪表盘\n\n- 贵州茅台: 买入")],
)
def test_aggregate_wechat_image_source_follows_layout(layout, expected_source):
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.notifier = _AggregateWechatNotifier()
    pipeline.config = SimpleNamespace(stock_email_groups=[], markdown_to_image_layout=layout)
    results = [SimpleNamespace(code="600519"), SimpleNamespace(code="000858")]

    with mock.patch("src.md2img.markdown_to_image", return_value=b"png") as mock_md2img:
        pipeline._send_notifications(results, ReportType.FULL)

    assert mock_md2img.call_args.args[0] == expected_source
    # 文本回退仍使用企业微信精简仪表盘（受 WECHAT_MAX_BYTES 限制）
    pipeline.notifier._send_wechat_report_image.assert_called_once_with(
        b"png", fallback_content="# 精简仪表盘\n\n- 贵州茅台: 买入"
    )


def test_aggregate_wechat_full_layout_renders_all_sections():
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.notifier = _AggregateWechatNotifier()
    pipeline.config = SimpleNamespace(stock_email_groups=[], markdown_to_image_layout="full")
    results = [SimpleNamespace(code="600519"), SimpleNamespace(code="000858")]
    captured = []

    with mock.patch("src.config.get_config", return_value=_layout_config("full")), \
            mock.patch.object(md2img, "_markdown_to_image_wkhtml", side_effect=_render_capture(captured)):
        pipeline._send_notifications(results, ReportType.FULL)

    assert len(captured) == 1
    _assert_full_sections(captured[0]["html"], STOCK_SECTION_TITLES)
    assert "五粮液" in captured[0]["html"]


# ---------------------------------------------------------------------------
# Web 分享按钮
# ---------------------------------------------------------------------------


class _FakeHistoryService:
    def __init__(self, result, markdown):
        self.result = result
        self.markdown = markdown

    def resolve_and_get_detail(self, record_id):
        return self.result

    def get_markdown_report(self, record_id):
        return self.markdown


def _patch_history(monkeypatch, layout: str):
    result = {"id": 30, "report_type": "detailed", "raw_result": STOCK_PAYLOAD, "context_snapshot": {}}
    service = _FakeHistoryService(result, SINGLE_FULL_REPORT)
    monkeypatch.setattr(history_endpoint, "HistoryService", lambda _db: service)
    monkeypatch.setattr(history_endpoint, "get_config", lambda: _layout_config(layout))


@pytest.mark.parametrize("layout", ["card", "full"])
def test_web_share_image_ignores_markdown_to_image_layout(monkeypatch, layout):
    _patch_history(monkeypatch, layout)
    captured = []
    with mock.patch("src.config.get_config", return_value=_layout_config(layout)), \
            mock.patch.object(md2img, "_markdown_to_image_wkhtml", side_effect=_render_capture(captured)):
        response = history_endpoint.get_history_share_image("30", db_manager=object())

    assert response.status_code == 200
    assert captured[0]["layout"] == "card"
    assert 'class="signal-row"' in captured[0]["html"]
    assert "检查清单" not in captured[0]["html"]


@pytest.mark.parametrize("layout", ["card", "full"])
def test_web_share_image_html_ignores_markdown_to_image_layout(monkeypatch, layout):
    _patch_history(monkeypatch, layout)

    body = history_endpoint.get_history_share_image_html("30", db_manager=object()).body.decode("utf-8")

    assert 'class="signal-row"' in body
    assert "检查清单" not in body


# ---------------------------------------------------------------------------
# full 版式超长图经企业微信压缩/切图后每张 <= 2MB
# ---------------------------------------------------------------------------


def _long_full_report(stock_count: int = 12) -> str:
    sections = "\n".join(_stock_section(f"样例股票{i}", f"6{i:05d}") for i in range(stock_count))
    return f"# 🎯 2026-11-01 决策仪表盘\n\n> 共分析 **{stock_count}** 只股票\n\n---\n\n{sections}"


def _assert_wechat_parts_within_limit(image_bytes: bytes) -> list:
    parts = _prepare_wechat_images(image_bytes)
    assert parts is not None, "超长图应被压缩/切图，而非回退文本"
    assert 1 <= len(parts) <= WECHAT_IMAGE_MAX_SEGMENTS
    for part in parts:
        assert len(part) <= WECHAT_IMAGE_MAX_BYTES
    return parts


def test_full_layout_long_image_rendered_by_playwright_fits_wechat_limit():
    """使用真实 Playwright Chromium 渲染 full 版式长图；无渲染器的环境跳过。"""

    if md2img._resolve_playwright_command() is None:
        pytest.skip("Playwright CLI 不可用，跳过真实渲染")
    markdown_text = _long_full_report()
    image_bytes = md2img._markdown_to_image_playwright(markdown_text, None, ShareImageBranding(), "full")
    if image_bytes is None:
        pytest.skip("Playwright Chromium 浏览器不可用，跳过真实渲染")

    image = Image.open(io.BytesIO(image_bytes))
    assert image.width == 1080
    assert image.height > 10000
    _assert_wechat_parts_within_limit(image_bytes)


def test_full_layout_sized_long_image_fits_wechat_limit():
    """确定性兜底：模拟 full 版式长图（1080 宽、超高、浅底细节、PNG > 2MB）。"""

    rng = np.random.default_rng(7)
    array = (200 + rng.integers(0, 40, (14000, 1080, 3))).astype(np.uint8)
    buffer = io.BytesIO()
    Image.fromarray(array).save(buffer, format="PNG")
    image_bytes = buffer.getvalue()
    assert len(image_bytes) > WECHAT_IMAGE_MAX_BYTES

    parts = _assert_wechat_parts_within_limit(image_bytes)
    heights = [Image.open(io.BytesIO(part)).height for part in parts]
    assert sum(heights) == 14000
