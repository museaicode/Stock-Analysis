# -*- coding: utf-8 -*-
"""Anspire AI Search 标准版 / 高级版 PRO 请求契约测试（离线）。"""

import logging
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

if "newspaper" not in sys.modules:
    mock_np = MagicMock()
    mock_np.Article = MagicMock()
    mock_np.Config = MagicMock()
    sys.modules["newspaper"] = mock_np

from src.config import Config
from src.search_service import (
    ANSPIRE_PRO_URL,
    ANSPIRE_STANDARD_URL,
    AnspireSearchProvider,
    SearchService,
    normalize_anspire_tier,
    normalize_anspire_top_k,
    parse_anspire_region_markets,
    resolve_anspire_region_mode,
    truncate_anspire_query,
)

BJ = timezone(timedelta(hours=8))
API_KEY = "sk-anspire-secret-key-123456"


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.text = ""
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._json


def _run(provider, json_data=None, **search_kwargs):
    """调用 provider.search 并返回 (response, url, params, headers)。"""
    fake = _FakeResponse(json_data=json_data if json_data is not None else {"results": []})
    with patch("src.search_service.requests.get", return_value=fake) as mock_get:
        response = provider.search(search_kwargs.pop("query", "贵州茅台 最新消息"), **search_kwargs)
    args, kwargs = mock_get.call_args
    return response, args[0], kwargs["params"], kwargs["headers"]


class TestAnspireConfigNormalization(unittest.TestCase):
    def test_tier_default_and_fallback(self):
        self.assertEqual(normalize_anspire_tier(None), "standard")
        self.assertEqual(normalize_anspire_tier(""), "standard")
        self.assertEqual(normalize_anspire_tier(" PRO "), "pro")
        with self.assertLogs("src.search_service", level="WARNING"):
            self.assertEqual(normalize_anspire_tier("premium"), "standard")

    def test_top_k_default_and_fallback(self):
        self.assertEqual(normalize_anspire_top_k(None), 10)
        self.assertEqual(normalize_anspire_top_k(""), 10)
        self.assertEqual(normalize_anspire_top_k("30"), 30)
        for bad in ("15", "0", "60", "abc", "-10"):
            with self.assertLogs("src.search_service", level="WARNING"):
                self.assertEqual(normalize_anspire_top_k(bad), 10, bad)

    def test_region_markets_default(self):
        self.assertEqual(
            parse_anspire_region_markets(None),
            {"cn": "domestic", "hk": "mixed", "topic": "domestic"},
        )

    def test_region_markets_override_and_invalid_entries(self):
        with self.assertLogs("src.search_service", level="WARNING") as logs:
            regions = parse_anspire_region_markets("HK:Overseas; us:mixed; xx:domestic; cn:global; bad")
        self.assertEqual(
            regions,
            {"cn": "domestic", "hk": "overseas", "topic": "domestic", "us": "mixed"},
        )
        self.assertEqual(len(logs.records), 3)

    def test_region_mode_mapping_standard(self):
        self.assertEqual(resolve_anspire_region_mode("domestic", "standard"), 0)
        self.assertEqual(resolve_anspire_region_mode("overseas", "standard"), 1)
        self.assertEqual(resolve_anspire_region_mode("mixed", "standard"), 2)

    def test_region_mode_mapping_pro(self):
        self.assertEqual(resolve_anspire_region_mode("domestic", "pro"), 0)
        self.assertEqual(resolve_anspire_region_mode("mixed", "pro"), 1)
        with self.assertLogs("src.search_service", level="WARNING"):
            self.assertEqual(resolve_anspire_region_mode("overseas", "pro"), 1)

    def test_config_reads_env(self):
        env = {
            "ANSPIRE_TIER": "pro",
            "ANSPIRE_REGION_MARKETS": "hk:domestic",
            "ANSPIRE_TOP_K": "20",
        }
        with patch.dict(os.environ, env, clear=False):
            Config._Config__instance = None
            try:
                config = Config._load_from_env()
            finally:
                Config._Config__instance = None
        self.assertEqual(config.anspire_tier, "pro")
        self.assertEqual(config.anspire_region_markets, "hk:domestic")
        self.assertEqual(config.anspire_top_k, "20")


class TestAnspireRequest(unittest.TestCase):
    def test_standard_defaults(self):
        provider = AnspireSearchProvider([API_KEY])
        _, url, params, headers = _run(provider, market="cn", max_results=5, days=3)
        self.assertEqual(url, ANSPIRE_STANDARD_URL)
        self.assertEqual(headers, {"Authorization": f"Bearer {API_KEY}"})
        self.assertEqual(params["top_k"], 10)
        self.assertEqual(params["region_mode"], 0)
        self.assertEqual(params["search_type"], "web")
        self.assertEqual(
            set(params),
            {"query", "top_k", "region_mode", "FromTime", "ToTime", "search_type"},
        )

    def test_standard_region_mode_per_market(self):
        provider = AnspireSearchProvider([API_KEY], region_markets="us:overseas")
        expected = {"cn": 0, "hk": 2, "topic": 0, "us": 1, None: 0, "jp": 2}
        for market, mode in expected.items():
            _, _, params, _ = _run(provider, market=market)
            self.assertEqual(params["region_mode"], mode, market)

    def test_pro_url_region_mode_and_no_search_type(self):
        with self.assertLogs("src.search_service", level="WARNING"):
            provider = AnspireSearchProvider(
                [API_KEY], tier="pro", region_markets="cn:domestic;hk:mixed;us:overseas"
            )
        for market, mode in {"cn": 0, "hk": 1, "topic": 0, "us": 1, "kr": 1}.items():
            _, url, params, _ = _run(provider, market=market)
            self.assertEqual(url, ANSPIRE_PRO_URL)
            self.assertEqual(params["region_mode"], mode, market)
            self.assertNotIn("search_type", params)

    def test_invalid_tier_falls_back_to_standard_url(self):
        with self.assertLogs("src.search_service", level="WARNING"):
            provider = AnspireSearchProvider([API_KEY], tier="enterprise")
        _, url, params, _ = _run(provider)
        self.assertEqual(url, ANSPIRE_STANDARD_URL)
        self.assertEqual(params["search_type"], "web")

    def test_top_k_configured_and_raised_for_large_requests(self):
        provider = AnspireSearchProvider([API_KEY], top_k="20")
        _, _, params, _ = _run(provider, max_results=5)
        self.assertEqual(params["top_k"], 20)
        _, _, params, _ = _run(provider, max_results=25)
        self.assertEqual(params["top_k"], 30)
        _, _, params, _ = _run(provider, max_results=80)
        self.assertEqual(params["top_k"], 50)

    def test_from_time_is_beijing_time(self):
        provider = AnspireSearchProvider([API_KEY])
        now_utc = datetime(2026, 1, 10, 20, 30, 15, tzinfo=timezone.utc)  # 北京时间 01-11 04:30:15
        params = provider.build_request_params("q", max_results=5, days=3, now=now_utc)
        self.assertEqual(params["ToTime"], "2026-01-11 04:30:15")
        self.assertEqual(params["FromTime"], "2026-01-08 04:30:15")

    def test_from_time_format_and_default_days(self):
        provider = AnspireSearchProvider([API_KEY], default_days=2)
        _, _, params, _ = _run(provider)  # 未传 days -> 新闻窗口
        from_time = datetime.strptime(params["FromTime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=BJ)
        to_time = datetime.strptime(params["ToTime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=BJ)
        self.assertEqual(to_time - from_time, timedelta(days=2))
        self.assertLess(abs((datetime.now(BJ) - to_time).total_seconds()), 120)

    def test_log_contains_context_but_not_api_key(self):
        provider = AnspireSearchProvider([API_KEY])
        with self.assertLogs("src.search_service", level="INFO") as logs:
            _run(provider, market="hk", json_data={"results": [{"title": "t", "url": "https://a.com"}]})
        joined = "\n".join(logs.output)
        self.assertNotIn(API_KEY, joined)
        summary = [line for line in logs.output if "请求完成" in line]
        self.assertEqual(len(summary), 1)
        for token in ("market=hk", "tier=standard", "region_mode=2", "top_k=10", "FromTime=", "returned=1"):
            self.assertIn(token, summary[0])


class TestAnspireQueryTruncation(unittest.TestCase):
    def test_short_query_unchanged(self):
        self.assertEqual(truncate_anspire_query("贵州茅台 600519 最新消息"), "贵州茅台 600519 最新消息")

    def test_long_query_keeps_name_and_code(self):
        query = "最新 公告 业绩 预告 机构 调研 评级 下调 回购 增持 减持 分红 " * 3 + "贵州茅台 600519"
        result = truncate_anspire_query(query, stock_name="贵州茅台", stock_code="600519")
        self.assertLessEqual(len(result), 64)
        self.assertIn("贵州茅台", result)
        self.assertIn("600519", result)

    def test_name_inside_oversized_token(self):
        query = "关于贵州茅台" + "非常长的没有空格的描述" * 10
        result = truncate_anspire_query(query, stock_name="贵州茅台", stock_code="600519")
        self.assertLessEqual(len(result), 64)
        self.assertTrue(result.startswith("贵州茅台"))

    def test_without_anchors_and_dangling_operator(self):
        query = "a" * 60 + " OR bbbbbbbbbb"
        result = truncate_anspire_query(query)
        self.assertLessEqual(len(result), 64)
        self.assertFalse(result.endswith("OR"))

    def test_request_query_is_truncated(self):
        provider = AnspireSearchProvider([API_KEY])
        long_query = "腾讯控股 00700 " + " ".join(["港股 最新 消息 业绩 回购"] * 10)
        _, _, params, _ = _run(
            provider, query=long_query, market="hk", stock_name="腾讯控股", stock_code="00700"
        )
        self.assertLessEqual(len(params["query"]), 64)
        self.assertIn("腾讯控股", params["query"])
        self.assertIn("00700", params["query"])


class TestAnspireResponseParsing(unittest.TestCase):
    def test_fields_mapping_filter_and_sort(self):
        now = datetime.now(BJ)
        fmt = "%Y-%m-%d %H:%M:%S"
        data = {
            "query": "贵州茅台",
            "Uuid": "abc",
            "results": [
                {"title": "旧闻", "content": "x", "url": "https://old.com/a", "score": 0.99,
                 "date": (now - timedelta(days=30)).strftime(fmt)},
                {"title": "较新", "content": "c1", "url": "https://www.sina.com.cn/1", "score": 0.5,
                 "date": (now - timedelta(days=1)).strftime(fmt)},
                {"title": "最新", "content": "c2" * 400, "url": "https://eastmoney.com/2", "score": 0.1,
                 "date": (now - timedelta(hours=1)).strftime("%Y/%m/%d %H:%M:%S")},
                {"title": "无日期", "content": "c3", "url": "https://x.com/3", "score": 0.8},
            ],
        }
        provider = AnspireSearchProvider([API_KEY])
        response, _, _, _ = _run(provider, json_data=data, days=3, max_results=10)
        self.assertTrue(response.success)
        self.assertEqual([r.title for r in response.results], ["最新", "较新", "无日期"])
        newest = response.results[0]
        self.assertEqual(newest.source, "eastmoney.com")
        self.assertTrue(newest.snippet.endswith("..."))
        self.assertLessEqual(len(newest.snippet), 503)
        self.assertRegex(newest.published_date, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        self.assertEqual(response.results[1].source, "sina.com.cn")
        self.assertIsNone(response.results[2].published_date)

    def test_max_results_applied_after_sort(self):
        now = datetime.now(BJ)
        items = [
            {"title": f"n{i}", "content": "c", "url": f"https://a.com/{i}",
             "date": (now - timedelta(hours=i)).strftime("%Y-%m-%d %H:%M:%S")}
            for i in range(10, 0, -1)
        ]
        provider = AnspireSearchProvider([API_KEY])
        response, _, _, _ = _run(provider, json_data={"results": items}, max_results=3, days=3)
        self.assertEqual([r.title for r in response.results], ["n1", "n2", "n3"])

    def test_missing_results_field_is_failure(self):
        provider = AnspireSearchProvider([API_KEY])
        response, _, _, _ = _run(provider, json_data={"query": "q", "Uuid": "u"})
        self.assertFalse(response.success)


class TestSearchServiceAnspireWiring(unittest.TestCase):
    def _service(self, **kwargs):
        return SearchService(anspire_keys=[API_KEY], news_max_age_days=3, **kwargs)

    def test_service_passes_tier_region_and_window(self):
        service = self._service(anspire_tier="pro", anspire_region_markets="hk:domestic", anspire_top_k="40")
        provider = service._providers[0]
        self.assertIsInstance(provider, AnspireSearchProvider)
        self.assertEqual(provider.tier, "pro")
        self.assertEqual(provider.top_k, 40)
        self.assertEqual(provider.region_mode_for_market("hk"), 0)
        self.assertEqual(provider.default_days, service.news_window_days)

    def test_stock_news_passes_market_and_stock_identity(self):
        service = self._service()
        provider = service._providers[0]
        with patch.object(provider, "_do_search", wraps=provider._do_search) as spy, \
                patch("src.search_service.requests.get", return_value=_FakeResponse(json_data={"results": []})):
            service.search_stock_news("hk00700", "腾讯控股", max_results=3)
        self.assertTrue(spy.called)
        kwargs = spy.call_args.kwargs
        self.assertEqual(kwargs["market"], "hk")
        self.assertEqual(kwargs["stock_name"], "腾讯控股")
        self.assertEqual(kwargs["stock_code"], "hk00700")

    def test_us_stock_still_skips_anspire_with_market_table(self):
        service = self._service(provider_markets="anspire:cn,hk,topic")
        self.assertEqual(service._providers_for_target("AAPL", "Apple"), [])
        self.assertEqual(len(service._providers_for_target("600519", "贵州茅台")), 1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    unittest.main()
