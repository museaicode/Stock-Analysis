# -*- coding: utf-8 -*-
"""Tests for SEARCH_PROVIDER_STRATEGY: per-market round-robin for stock news search."""

import logging
import os
import sys
import threading
import unittest
from collections import Counter
from datetime import datetime
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
    SearchResult,
    SearchService,
    SerpAPISearchProvider,
    TavilySearchProvider,
    next_provider_round_robin_offset,
    parse_search_provider_strategy,
    reset_provider_round_robin_cursors,
    rotate_sequence,
)

_LOGGER_NAME = "src.search_service"
# The user's real-world routing: US uses Tavily/Brave/SerpAPI, Anspire only cn/hk/topic.
_USER_MARKETS = "anspire:cn,hk,topic;serpapi:us;brave:us;tavily:us"
_US_ORDER = [TavilySearchProvider, BraveSearchProvider, SerpAPISearchProvider]


def _failed(name: str) -> SearchResponse:
    return SearchResponse(query="q", results=[], provider=name, success=False, error_message="mock failure")


def _apple_news(name: str) -> SearchResponse:
    today = datetime.now().date().isoformat()
    return SearchResponse(
        query="q",
        results=[
            SearchResult(
                title="Apple (AAPL) shares rise after earnings",
                snippet="Apple Inc. AAPL stock moved higher on strong iPhone demand.",
                url=f"https://news.example.com/{name}/aapl",
                source="news.example.com",
                published_date=today,
            )
        ],
        provider=name,
        success=True,
    )


def _build_service(strategy=None, markets=_USER_MARKETS, **extra_keys) -> SearchService:
    keys = dict(
        anspire_keys=["anspire-key"],
        tavily_keys=["tavily-key"],
        brave_keys=["brave-key"],
        serpapi_keys=["serpapi-key"],
    )
    keys.update(extra_keys)
    return SearchService(
        searxng_public_instances_enabled=False,
        provider_markets=markets,
        provider_strategy=strategy,
        **keys,
    )


def _mock_all(service: SearchService, factory) -> None:
    for provider in service._providers:
        provider.search = MagicMock(side_effect=lambda *a, _n=provider.name, **k: factory(_n))


class ParseSearchProviderStrategyTestCase(unittest.TestCase):
    def test_parses_markets_case_insensitive(self):
        self.assertEqual(
            parse_search_provider_strategy("US:Round_Robin; cn:sequential\nhk : sequential;topic:sequential"),
            {"us": "round_robin", "cn": "sequential", "hk": "sequential", "topic": "sequential"},
        )

    def test_empty_means_no_overrides(self):
        self.assertEqual(parse_search_provider_strategy(""), {})
        self.assertEqual(parse_search_provider_strategy(None), {})
        self.assertEqual(parse_search_provider_strategy(" ; ;"), {})

    def test_invalid_strategy_warns_and_falls_back_to_sequential(self):
        with self.assertLogs(_LOGGER_NAME, level=logging.WARNING) as logs:
            result = parse_search_provider_strategy("us:random;cn:round_robin")
        self.assertEqual(result, {"us": "sequential", "cn": "round_robin"})
        self.assertTrue(any("SEARCH_PROVIDER_STRATEGY" in line and "random" in line for line in logs.output))

    def test_unknown_market_and_bad_format_are_ignored_with_warning(self):
        with self.assertLogs(_LOGGER_NAME, level=logging.WARNING) as logs:
            result = parse_search_provider_strategy("mars:round_robin;round_robin;us:round_robin")
        self.assertEqual(result, {"us": "round_robin"})
        self.assertEqual(len(logs.output), 2)


class RotateSequenceTestCase(unittest.TestCase):
    def test_rotate(self):
        self.assertEqual(rotate_sequence(["a", "b", "c"], 0), ["a", "b", "c"])
        self.assertEqual(rotate_sequence(["a", "b", "c"], 1), ["b", "c", "a"])
        self.assertEqual(rotate_sequence(["a", "b", "c"], 5), ["c", "a", "b"])
        self.assertEqual(rotate_sequence([], 3), [])


class StockNewsStrategyTestCase(unittest.TestCase):
    def setUp(self):
        reset_provider_round_robin_cursors()
        self.addCleanup(reset_provider_round_robin_cursors)
        patcher = patch("src.search_service.record_provider_run_started")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _ordered_types(self, service, code, name):
        _market, _strategy, providers = service._ordered_providers_for_stock_news(code, name)
        return [type(p) for p in providers]

    def test_default_is_sequential_and_unchanged(self):
        service = _build_service()
        for _ in range(4):
            self.assertEqual(self._ordered_types(service, "AAPL", "Apple"), _US_ORDER)
        market, strategy, _ = service._ordered_providers_for_stock_news("AAPL", "Apple")
        self.assertEqual((market, strategy), ("us", "sequential"))

    def test_round_robin_rotates_start_per_request(self):
        service = _build_service("us:round_robin;cn:sequential;hk:sequential;topic:sequential")
        orders = [self._ordered_types(service, "AAPL", "Apple") for _ in range(4)]
        self.assertEqual(
            orders,
            [
                [TavilySearchProvider, BraveSearchProvider, SerpAPISearchProvider],
                [BraveSearchProvider, SerpAPISearchProvider, TavilySearchProvider],
                [SerpAPISearchProvider, TavilySearchProvider, BraveSearchProvider],
                [TavilySearchProvider, BraveSearchProvider, SerpAPISearchProvider],
            ],
        )

    def test_round_robin_respects_provider_markets(self):
        """US never gets Anspire; cn/hk never get US-only providers, even with round_robin everywhere."""
        service = _build_service("us:round_robin;cn:round_robin;hk:round_robin", bocha_keys=["bocha-key"])
        for _ in range(6):
            us = self._ordered_types(service, "AAPL", "Apple")
            self.assertNotIn(AnspireSearchProvider, us)
            self.assertEqual(sorted(t.__name__ for t in us), sorted(t.__name__ for t in _US_ORDER + [BochaSearchProvider]))
            for code, name in (("600519", "贵州茅台"), ("hk00700", "腾讯控股")):
                cn = self._ordered_types(service, code, name)
                self.assertEqual(set(cn), {AnspireSearchProvider, BochaSearchProvider})

    def test_counters_are_per_market(self):
        service = _build_service("us:round_robin;cn:round_robin", bocha_keys=["bocha-key"])
        # Advance the cn counter; the us rotation must not be affected.
        self._ordered_types(service, "600519", "贵州茅台")
        self._ordered_types(service, "600519", "贵州茅台")
        self.assertEqual(self._ordered_types(service, "AAPL", "Apple")[0], BochaSearchProvider)
        self.assertEqual(self._ordered_types(service, "AAPL", "Apple")[0], TavilySearchProvider)
        # Unlisted market (hk) stays sequential.
        self.assertEqual(
            self._ordered_types(service, "hk00700", "腾讯控股"),
            [AnspireSearchProvider, BochaSearchProvider],
        )

    def test_counter_survives_service_rebuild(self):
        service = _build_service("us:round_robin")
        self._ordered_types(service, "AAPL", "Apple")
        rebuilt = SearchService(**service._constructor_kwargs)
        self.assertEqual(rebuilt._constructor_kwargs["provider_strategy"], "us:round_robin")
        self.assertEqual(self._ordered_types(rebuilt, "AAPL", "Apple")[0], BraveSearchProvider)

    def test_unavailable_provider_is_skipped_not_double_counted(self):
        service = _build_service("us:round_robin")
        brave = next(p for p in service._providers if isinstance(p, BraveSearchProvider))
        brave._api_keys = []  # simulate missing key
        orders = [self._ordered_types(service, "AAPL", "Apple") for _ in range(4)]
        self.assertEqual(
            [o[0] for o in orders],
            [TavilySearchProvider, SerpAPISearchProvider, TavilySearchProvider, SerpAPISearchProvider],
        )
        self.assertTrue(all(BraveSearchProvider not in o for o in orders))

    def test_search_stock_news_uses_rotated_start_provider(self):
        service = _build_service("us:round_robin")
        _mock_all(service, _apple_news)
        used = []
        for _ in range(3):
            service._cache.clear()
            used.append(service.search_stock_news("AAPL", "Apple").provider)
        self.assertEqual(used, ["Tavily", "Brave", "SerpAPI"])
        self.assertFalse(
            next(p for p in service._providers if isinstance(p, AnspireSearchProvider)).search.called
        )

    def test_failure_falls_through_to_remaining_providers(self):
        service = _build_service("us:round_robin")
        # Advance so this request starts at Brave.
        next_provider_round_robin_offset("us")
        call_order = []

        def factory(name):
            call_order.append(name)
            return _failed(name) if name == "Brave" else _apple_news(name)

        _mock_all(service, factory)
        response = service.search_stock_news("AAPL", "Apple")
        self.assertEqual(call_order, ["Brave", "SerpAPI"])
        self.assertEqual(response.provider, "SerpAPI")
        self.assertTrue(response.results)

    def test_all_failures_try_every_filtered_provider_once(self):
        service = _build_service("us:round_robin")
        next_provider_round_robin_offset("us")
        next_provider_round_robin_offset("us")
        call_order = []

        def factory(name):
            call_order.append(name)
            return _failed(name)

        _mock_all(service, factory)
        response = service.search_stock_news("AAPL", "Apple")
        self.assertEqual(call_order, ["SerpAPI", "Tavily", "Brave"])
        self.assertFalse(response.success)

    def test_logs_market_strategy_and_used_provider(self):
        service = _build_service("us:round_robin")
        _mock_all(service, _apple_news)
        with self.assertLogs(_LOGGER_NAME, level=logging.INFO) as logs:
            service.search_stock_news("AAPL", "Apple")
        joined = "\n".join(logs.output)
        self.assertIn("市场=us, 策略=round_robin, 搜索源顺序=Tavily -> Brave -> SerpAPI", joined)
        self.assertIn("市场=us, 策略=round_robin, 实际使用搜索源=Tavily", joined)

    def test_concurrent_requests_rotate_evenly(self):
        service = _build_service("us:round_robin")
        threads_count, per_thread = 8, 75
        firsts = Counter()
        lock = threading.Lock()
        barrier = threading.Barrier(threads_count)

        def worker():
            barrier.wait()
            local = Counter()
            for _ in range(per_thread):
                local[self._ordered_types(service, "AAPL", "Apple")[0]] += 1
            with lock:
                firsts.update(local)

        threads = [threading.Thread(target=worker) for _ in range(threads_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        total = threads_count * per_thread
        self.assertEqual(sum(firsts.values()), total)
        self.assertEqual(set(firsts.values()), {total // 3})
        self.assertEqual(next_provider_round_robin_offset("us"), total)

    def test_concurrent_offsets_are_unique(self):
        results = []
        lock = threading.Lock()

        def worker():
            local = [next_provider_round_robin_offset("hk") for _ in range(500)]
            with lock:
                results.extend(local)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(results), list(range(5000)))


class SearchProviderStrategyConfigTestCase(unittest.TestCase):
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
        config = self._load({"SEARCH_PROVIDER_STRATEGY": "  us:round_robin;cn:sequential  "})
        self.assertEqual(config.search_provider_strategy, "us:round_robin;cn:sequential")

    def test_defaults_to_empty(self):
        self.assertEqual(self._load({}).search_provider_strategy, "")


if __name__ == "__main__":
    unittest.main()
