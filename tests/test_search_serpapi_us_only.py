# -*- coding: utf-8 -*-
"""Tests for SERPAPI_US_ONLY: SerpAPI only participates in US-stock searches."""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Mock newspaper before search_service import (optional dependency)
if "newspaper" not in sys.modules:
    mock_np = MagicMock()
    mock_np.Article = MagicMock()
    mock_np.Config = MagicMock()
    sys.modules["newspaper"] = mock_np

from src.config import Config
from src.search_service import (
    BochaSearchProvider,
    SearchResponse,
    SearchService,
    SerpAPISearchProvider,
)


def _failed_response(provider_name: str) -> SearchResponse:
    return SearchResponse(
        query="q",
        results=[],
        provider=provider_name,
        success=False,
        error_message="mock failure",
    )


class SerpApiUsOnlySearchServiceTestCase(unittest.TestCase):
    def _make_service(self, serpapi_us_only: bool = True) -> SearchService:
        service = SearchService(
            bocha_keys=["bocha-key"],
            serpapi_keys=["serpapi-key"],
            serpapi_us_only=serpapi_us_only,
        )
        # Every provider fails so the service walks the full provider chain.
        for provider in service._providers:
            provider.search = MagicMock(return_value=_failed_response(provider.name))
        return service

    @staticmethod
    def _provider(service: SearchService, cls):
        return next(p for p in service._providers if isinstance(p, cls))

    def _assert_serpapi_called(self, service: SearchService, expected: bool) -> None:
        serpapi = self._provider(service, SerpAPISearchProvider)
        bocha = self._provider(service, BochaSearchProvider)
        self.assertTrue(bocha.search.called, "non-SerpAPI providers should still be used")
        self.assertEqual(serpapi.search.called, expected)

    # ---- provider selection -------------------------------------------------

    def test_providers_for_target_keeps_order_and_filters_serpapi(self):
        service = self._make_service()
        all_providers = list(service._providers)
        self.assertEqual(service._providers_for_target("AAPL", "Apple"), all_providers)
        self.assertEqual(
            service._providers_for_target("600519", "贵州茅台"),
            [p for p in all_providers if not isinstance(p, SerpAPISearchProvider)],
        )
        # No target (topic search) is treated as non-US.
        self.assertNotIn(
            SerpAPISearchProvider,
            [type(p) for p in service._providers_for_target()],
        )

    def test_disabled_keeps_serpapi_for_all_targets(self):
        service = self._make_service(serpapi_us_only=False)
        self.assertEqual(service._providers_for_target("600519", "贵州茅台"), service._providers)
        self.assertEqual(service._providers_for_target(), service._providers)

    def test_instance_created_via_new_is_compatible(self):
        service = SearchService.__new__(SearchService)
        service._providers = [MagicMock()]
        self.assertEqual(service._providers_for_target("600519", "贵州茅台"), service._providers)

    def test_is_us_search_target(self):
        self.assertTrue(SearchService._is_us_search_target("AAPL", "Apple"))
        self.assertTrue(SearchService._is_us_search_target("BRK.B", "Berkshire"))
        self.assertTrue(SearchService._is_us_search_target("market", "美股市场"))
        self.assertTrue(SearchService._is_us_search_target("market", "US Market"))
        self.assertFalse(SearchService._is_us_search_target("market", "大盘"))
        self.assertFalse(SearchService._is_us_search_target("market", "港股市场"))
        self.assertFalse(SearchService._is_us_search_target("600519", "贵州茅台"))
        self.assertFalse(SearchService._is_us_search_target("hk00700", "腾讯控股"))

    # ---- end-to-end entry points --------------------------------------------

    def test_a_share_news_skips_serpapi(self):
        service = self._make_service()
        service.search_stock_news("600519", "贵州茅台")
        self._assert_serpapi_called(service, False)

    def test_hk_news_skips_serpapi(self):
        service = self._make_service()
        service.search_stock_news("hk00700", "腾讯控股")
        self._assert_serpapi_called(service, False)

    def test_non_us_market_review_skips_serpapi(self):
        service = self._make_service()
        service.search_stock_news("market", "大盘", max_results=3, focus_keywords=["A股", "收盘"])
        self._assert_serpapi_called(service, False)

    def test_topic_news_skips_serpapi(self):
        service = self._make_service()
        service.search_topic_news("机器人", focus_keywords=["机器人", "概念股"])
        self._assert_serpapi_called(service, False)

    def test_us_stock_news_uses_serpapi(self):
        service = self._make_service()
        service.search_stock_news("AAPL", "Apple")
        self._assert_serpapi_called(service, True)

    def test_us_dotted_ticker_news_uses_serpapi(self):
        service = self._make_service()
        service.search_stock_news("BRK.B", "Berkshire Hathaway")
        self._assert_serpapi_called(service, True)

    def test_us_market_review_uses_serpapi(self):
        service = self._make_service()
        service.search_stock_news("market", "US market", max_results=3, focus_keywords=["S&P", "Nasdaq"])
        self._assert_serpapi_called(service, True)

    def test_a_share_events_and_price_fallback_skip_serpapi(self):
        service = self._make_service()
        service.search_stock_events("600519", "贵州茅台")
        service.search_stock_price_fallback("600519", "贵州茅台")
        self._assert_serpapi_called(service, False)

    def test_us_events_use_serpapi(self):
        service = self._make_service()
        service.search_stock_events("AAPL", "Apple")
        self._assert_serpapi_called(service, True)

    def test_comprehensive_intel_rotation_excludes_serpapi_for_a_share(self):
        service = self._make_service()
        service.search_comprehensive_intel("600519", "贵州茅台", max_searches=5)
        self._assert_serpapi_called(service, False)

    def test_comprehensive_intel_rotation_includes_serpapi_for_us(self):
        service = self._make_service()
        service.search_comprehensive_intel("AAPL", "Apple", max_searches=5)
        self._assert_serpapi_called(service, True)

    # ---- reconstruction ------------------------------------------------------

    def test_constructor_kwargs_rebuild_preserves_flag(self):
        service = self._make_service()
        self.assertTrue(service._constructor_kwargs["serpapi_us_only"])
        rebuilt = SearchService(**service._constructor_kwargs)
        self.assertTrue(rebuilt._serpapi_us_only)
        self.assertNotIn(
            SerpAPISearchProvider,
            [type(p) for p in rebuilt._providers_for_target("600519", "贵州茅台")],
        )

    def test_serpapi_only_provider_logs_warning(self):
        with self.assertLogs("src.search_service", level="WARNING") as captured:
            SearchService(serpapi_keys=["serpapi-key"], serpapi_us_only=True)
        self.assertTrue(any("SERPAPI_US_ONLY" in line for line in captured.output))


class SerpApiUsOnlyConfigTestCase(unittest.TestCase):
    def tearDown(self):
        Config.reset_instance()

    def _load(self, env):
        with patch("src.config.setup_env"), patch.object(
            Config, "_parse_litellm_yaml", return_value=[]
        ), patch.object(Config, "_parse_stock_email_groups", return_value=[]), patch.dict(
            os.environ, env, clear=True
        ):
            return Config._load_from_env()

    def test_parses_true(self):
        self.assertTrue(self._load({"SERPAPI_US_ONLY": "true"}).serpapi_us_only)

    def test_parses_false(self):
        self.assertFalse(self._load({"SERPAPI_US_ONLY": "false"}).serpapi_us_only)

    def test_defaults_to_false_when_unset(self):
        self.assertFalse(self._load({}).serpapi_us_only)


if __name__ == "__main__":
    unittest.main()
