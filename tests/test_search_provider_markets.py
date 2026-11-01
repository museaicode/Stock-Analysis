# -*- coding: utf-8 -*-
"""Tests for SEARCH_PROVIDER_MARKETS: per-market search provider routing."""

import logging
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
    AnspireSearchProvider,
    BochaSearchProvider,
    BraveSearchProvider,
    SearchResponse,
    SearchService,
    SerpAPISearchProvider,
    parse_search_provider_markets,
)

_LOGGER_NAME = "src.search_service"
_NO_PROVIDER_MARKER = "没有可用的搜索服务"


def _failed_response(provider_name: str) -> SearchResponse:
    return SearchResponse(
        query="q",
        results=[],
        provider=provider_name,
        success=False,
        error_message="mock failure",
    )


def _mock_failing_search(service: SearchService) -> None:
    # Every provider fails so the service walks the full provider chain.
    for provider in service._providers:
        provider.search = MagicMock(return_value=_failed_response(provider.name))


def _types(providers):
    return [type(p) for p in providers]


class ParseSearchProviderMarketsTestCase(unittest.TestCase):
    def test_parses_normal_entries(self):
        result = parse_search_provider_markets("Anspire:cn,HK,topic; serpapi:us\nbrave : us")
        self.assertEqual(
            result,
            {
                "anspire": frozenset({"cn", "hk", "topic"}),
                "serpapi": frozenset({"us"}),
                "brave": frozenset({"us"}),
            },
        )

    def test_empty_or_none_returns_empty(self):
        self.assertEqual(parse_search_provider_markets(""), {})
        self.assertEqual(parse_search_provider_markets(None), {})
        self.assertEqual(parse_search_provider_markets("  ;  ; "), {})

    def test_all_or_star_removes_restriction(self):
        self.assertEqual(parse_search_provider_markets("brave:all"), {})
        self.assertEqual(parse_search_provider_markets("brave:us;brave:*"), {})
        self.assertEqual(
            parse_search_provider_markets("brave:us,all;serpapi:us"),
            {"serpapi": frozenset({"us"})},
        )

    def test_invalid_entries_warn_and_are_ignored(self):
        with self.assertLogs(_LOGGER_NAME, level="WARNING") as captured:
            result = parse_search_provider_markets(
                "brave;unknown:us;serpapi:mars;:us;tavily:;bocha:cn,mars;serpapi:us"
            )
        self.assertEqual(
            result,
            {"serpapi": frozenset({"us"}), "bocha": frozenset({"cn"})},
        )
        joined = "\n".join(captured.output)
        self.assertIn("brave", joined)
        self.assertIn("unknown", joined)
        self.assertIn("mars", joined)


class SearchTargetMarketTestCase(unittest.TestCase):
    def test_market_detection(self):
        cases = {
            ("600519", "贵州茅台"): "cn",
            ("sh000016", "上证50"): "cn",
            ("hk00700", "腾讯控股"): "hk",
            ("00700", "腾讯控股"): "hk",
            ("00700.HK", "腾讯控股"): "hk",
            ("AAPL", "Apple"): "us",
            ("BRK.B", "Berkshire"): "us",
            ("7203.T", "Toyota"): "jp",
            ("005930.KS", "Samsung"): "kr",
            ("2330.TW", "TSMC"): "tw",
            ("market", "美股市场"): "us",
            ("market", "US Market"): "us",
            ("market", "港股市场"): "hk",
            ("market", "hk market"): "hk",
            ("market", "大盘"): "cn",
            ("market", "A-share Market"): "cn",
            ("market", "日本股市"): "jp",
            ("market", "Japan Stock Market"): "jp",
            ("market", "韩国股市"): "kr",
            ("market", "korea stock market"): "kr",
            ("market", "其他市场"): "cn",
            ("", None): "topic",
            (None, None): "topic",
        }
        for (code, name), expected in cases.items():
            with self.subTest(code=code, name=name):
                self.assertEqual(SearchService._search_target_market(code, name), expected)

    def test_is_us_search_target_follows_market(self):
        self.assertTrue(SearchService._is_us_search_target("AAPL", "Apple"))
        self.assertTrue(SearchService._is_us_search_target("market", "美股市场"))
        self.assertFalse(SearchService._is_us_search_target("hk00700", "腾讯控股"))
        self.assertFalse(SearchService._is_us_search_target("market", "港股市场"))


class SearchProviderMarketsRoutingTestCase(unittest.TestCase):
    def test_us_only_table_filters_non_us_targets(self):
        service = SearchService(
            anspire_keys=["anspire-key"],
            bocha_keys=["bocha-key"],
            brave_keys=["brave-key"],
            serpapi_keys=["serpapi-key"],
            provider_markets="serpapi:us;brave:us",
        )
        all_providers = list(service._providers)
        expected_non_us = [AnspireSearchProvider, BochaSearchProvider]
        self.assertEqual(_types(service._providers_for_target("600519", "贵州茅台")), expected_non_us)
        self.assertEqual(_types(service._providers_for_target("hk00700", "腾讯控股")), expected_non_us)
        self.assertEqual(_types(service._providers_for_target()), expected_non_us)
        self.assertEqual(service._providers_for_target("AAPL", "Apple"), all_providers)

    def test_unconfigured_keeps_all_providers(self):
        service = SearchService(
            bocha_keys=["bocha-key"],
            brave_keys=["brave-key"],
            serpapi_keys=["serpapi-key"],
        )
        self.assertEqual(service._provider_markets, {})
        for code, name in (("600519", "贵州茅台"), ("AAPL", "Apple"), (None, None)):
            self.assertEqual(service._providers_for_target(code, name), service._providers)

    def test_serpapi_us_only_merges_into_table(self):
        service = SearchService(
            bocha_keys=["bocha-key"],
            brave_keys=["brave-key"],
            serpapi_keys=["serpapi-key"],
            serpapi_us_only=True,
            provider_markets="brave:us",
        )
        self.assertEqual(service._provider_markets["serpapi"], frozenset({"us"}))
        self.assertEqual(service._provider_markets["brave"], frozenset({"us"}))
        self.assertEqual(
            _types(service._providers_for_target("600519", "贵州茅台")),
            [BochaSearchProvider],
        )

    def test_explicit_serpapi_entry_overrides_us_only(self):
        service = SearchService(
            bocha_keys=["bocha-key"],
            serpapi_keys=["serpapi-key"],
            serpapi_us_only=True,
            provider_markets="serpapi:cn,us",
        )
        self.assertEqual(service._provider_markets["serpapi"], frozenset({"cn", "us"}))
        self.assertIn(
            SerpAPISearchProvider,
            _types(service._providers_for_target("600519", "贵州茅台")),
        )
        self.assertNotIn(
            SerpAPISearchProvider,
            _types(service._providers_for_target("hk00700", "腾讯控股")),
        )

    def test_constructor_kwargs_rebuild_preserves_table(self):
        service = SearchService(
            bocha_keys=["bocha-key"],
            brave_keys=["brave-key"],
            provider_markets="brave:us",
        )
        self.assertEqual(service._constructor_kwargs["provider_markets"], "brave:us")
        rebuilt = SearchService(**service._constructor_kwargs)
        self.assertEqual(rebuilt._provider_markets, {"brave": frozenset({"us"})})
        self.assertEqual(
            _types(rebuilt._providers_for_target("600519", "贵州茅台")),
            [BochaSearchProvider],
        )

    def test_instance_created_via_new_falls_back_to_serpapi_us_only(self):
        serpapi = SerpAPISearchProvider(["serpapi-key"])
        bocha = BochaSearchProvider(["bocha-key"])
        service = SearchService.__new__(SearchService)
        service._providers = [bocha, serpapi]
        self.assertEqual(service._providers_for_target("600519", "贵州茅台"), [bocha, serpapi])
        service._serpapi_us_only = True
        self.assertEqual(service._providers_for_target("600519", "贵州茅台"), [bocha])
        self.assertEqual(service._providers_for_target("AAPL", "Apple"), [bocha, serpapi])

    def test_brave_us_only_warns_about_uncovered_markets(self):
        with self.assertLogs(_LOGGER_NAME, level="WARNING") as captured:
            SearchService(brave_keys=["brave-key"], provider_markets="brave:us")
        warnings = [line for line in captured.output if _NO_PROVIDER_MARKER in line]
        self.assertEqual(len(warnings), 1)
        self.assertIn("cn", warnings[0])
        self.assertIn("hk", warnings[0])
        self.assertNotIn("us", warnings[0].split(_NO_PROVIDER_MARKER)[-1])

    def test_unconfigured_provider_in_table_is_reported_inactive(self):
        with self.assertLogs(_LOGGER_NAME, level="INFO") as captured:
            SearchService(bocha_keys=["bocha-key"], provider_markets="tavily:us")
        self.assertTrue(
            any("tavily" in line and "暂不生效" in line for line in captured.output)
        )


class PracticalScenarioTestCase(unittest.TestCase):
    """anspire:cn,hk,topic;serpapi:us;brave:us with Anspire/Brave/SerpAPI configured."""

    TABLE = "anspire:cn,hk,topic;serpapi:us;brave:us"

    def _make_service(self) -> SearchService:
        service = SearchService(
            anspire_keys=["anspire-key"],
            brave_keys=["brave-key"],
            serpapi_keys=["serpapi-key"],
            provider_markets=self.TABLE,
        )
        _mock_failing_search(service)
        return service

    @staticmethod
    def _provider(service: SearchService, cls):
        return next(p for p in service._providers if isinstance(p, cls))

    def test_non_us_targets_only_use_anspire(self):
        service = self._make_service()
        for code, name in (("600519", "贵州茅台"), ("hk00700", "腾讯控股"), (None, None)):
            with self.subTest(code=code):
                self.assertEqual(
                    _types(service._providers_for_target(code, name)),
                    [AnspireSearchProvider],
                )

    def test_us_targets_skip_anspire(self):
        service = self._make_service()
        for code, name in (("AAPL", "Apple"), ("market", "美股市场")):
            with self.subTest(code=code, name=name):
                providers = _types(service._providers_for_target(code, name))
                self.assertEqual(providers, [BraveSearchProvider, SerpAPISearchProvider])
                self.assertNotIn(AnspireSearchProvider, providers)

    def test_us_comprehensive_intel_never_calls_anspire(self):
        service = self._make_service()
        service.search_comprehensive_intel("AAPL", "Apple", max_searches=5)
        self.assertFalse(self._provider(service, AnspireSearchProvider).search.called)
        self.assertTrue(self._provider(service, BraveSearchProvider).search.called)

    def test_us_stock_news_never_calls_anspire(self):
        service = self._make_service()
        service.search_stock_news("AAPL", "Apple")
        self.assertFalse(self._provider(service, AnspireSearchProvider).search.called)
        self.assertTrue(self._provider(service, BraveSearchProvider).search.called)

    def test_a_share_comprehensive_intel_skips_us_providers(self):
        service = self._make_service()
        service.search_comprehensive_intel("600519", "贵州茅台", max_searches=5)
        self.assertTrue(self._provider(service, AnspireSearchProvider).search.called)
        self.assertFalse(self._provider(service, BraveSearchProvider).search.called)
        self.assertFalse(self._provider(service, SerpAPISearchProvider).search.called)

    def test_startup_does_not_warn_about_uncovered_markets(self):
        logger = logging.getLogger(_LOGGER_NAME)
        with self.assertLogs(logger, level="INFO") as captured:
            SearchService(
                anspire_keys=["anspire-key"],
                brave_keys=["brave-key"],
                serpapi_keys=["serpapi-key"],
                provider_markets=self.TABLE,
            )
        self.assertFalse(any(_NO_PROVIDER_MARKER in line for line in captured.output))


class SearchProviderMarketsConfigTestCase(unittest.TestCase):
    def tearDown(self):
        Config.reset_instance()

    def _load(self, env):
        with patch("src.config.setup_env"), patch.object(
            Config, "_parse_litellm_yaml", return_value=[]
        ), patch.object(Config, "_parse_stock_email_groups", return_value=[]), patch.dict(
            os.environ, env, clear=True
        ):
            return Config._load_from_env()

    def test_parses_value(self):
        config = self._load({"SEARCH_PROVIDER_MARKETS": "  serpapi:us;brave:us  "})
        self.assertEqual(config.search_provider_markets, "serpapi:us;brave:us")

    def test_defaults_to_empty(self):
        self.assertEqual(self._load({}).search_provider_markets, "")


if __name__ == "__main__":
    unittest.main()
