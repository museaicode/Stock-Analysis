# -*- coding: utf-8 -*-
"""Tests: US daily routing (TickFlow first, FINNHUB_DAILY_ENABLED) and
US_REALTIME_SOURCE_PRIORITY realtime routing."""

import logging
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data_provider.base import DataFetcherManager, DataFetchError
from data_provider.finnhub_fetcher import FinnhubFetcher
from data_provider.realtime_types import RealtimeSource, UnifiedRealtimeQuote


def _daily_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"date": "2026-05-01", "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0,
             "volume": 1000, "amount": 101000.0, "pct_chg": 1.0},
        ]
    )


def _fetcher(name: str, priority: int, *, daily=None, daily_error=None, quote=None) -> MagicMock:
    fetcher = MagicMock()
    fetcher.name = name
    fetcher.priority = priority
    fetcher.is_available_for_request.return_value = True
    if daily_error is not None:
        fetcher.get_daily_data.side_effect = daily_error
    else:
        fetcher.get_daily_data.return_value = daily
    fetcher.get_realtime_quote.return_value = quote
    return fetcher


def _quote(price=190.0, **fields) -> UnifiedRealtimeQuote:
    return UnifiedRealtimeQuote(code="AAPL", source=RealtimeSource.FALLBACK, price=price, **fields)


class _DailyRoutingBase(unittest.TestCase):
    def setUp(self):
        DataFetcherManager.reset_daily_source_health()

    def tearDown(self):
        DataFetcherManager.reset_daily_source_health()

    def _call_order(self, manager, fetchers, code="AAPL"):
        calls = []
        for fetcher in fetchers:
            original = fetcher.get_daily_data.side_effect

            def _record(*args, _name=fetcher.name, _orig=original, _f=fetcher, **kwargs):
                calls.append(_name)
                if _orig is not None:
                    raise _orig
                return _f.get_daily_data.return_value

            fetcher.get_daily_data.side_effect = _record
        try:
            manager.get_daily_data(code, start_date="2026-05-01", end_date="2026-05-08")
        except DataFetchError:
            pass
        return calls


@patch("src.config.get_config")
class UsDailyRoutingTestCase(_DailyRoutingBase):
    def _all_failing(self, **priorities):
        defaults = {
            "TickFlowFetcher": 2,
            "FinnhubFetcher": 2,
            "AlphaVantageFetcher": 3,
            "YfinanceFetcher": 4,
            "LongbridgeFetcher": 5,
        }
        defaults.update(priorities)
        return [
            _fetcher(name, prio, daily_error=DataFetchError(f"{name} down"))
            for name, prio in defaults.items()
        ]

    def test_default_order_skips_finnhub_candle(self, mock_get_config):
        mock_get_config.return_value = SimpleNamespace(finnhub_daily_enabled=False)
        fetchers = self._all_failing()
        manager = DataFetcherManager(fetchers=fetchers)
        with patch.object(manager, "_longbridge_preferred", return_value=False):
            calls = self._call_order(manager, fetchers)
        self.assertEqual(
            calls, ["TickFlowFetcher", "AlphaVantageFetcher", "YfinanceFetcher", "LongbridgeFetcher"]
        )
        fetchers[1].get_daily_data.assert_not_called()

    def test_default_config_without_flag_attr_skips_finnhub(self, mock_get_config):
        mock_get_config.return_value = SimpleNamespace()
        fetchers = self._all_failing()
        manager = DataFetcherManager(fetchers=fetchers)
        with patch.object(manager, "_longbridge_preferred", return_value=False):
            calls = self._call_order(manager, fetchers)
        self.assertNotIn("FinnhubFetcher", calls)

    def test_tickflow_success_returns_first(self, mock_get_config):
        mock_get_config.return_value = SimpleNamespace(finnhub_daily_enabled=False)
        tickflow = _fetcher("TickFlowFetcher", 2, daily=_daily_df())
        finnhub = _fetcher("FinnhubFetcher", 2, daily=_daily_df())
        yfinance = _fetcher("YfinanceFetcher", 4, daily=_daily_df())
        manager = DataFetcherManager(fetchers=[finnhub, yfinance, tickflow])
        with patch.object(manager, "_longbridge_preferred", return_value=False):
            _, source = manager.get_daily_data("AAPL", start_date="2026-05-01", end_date="2026-05-08")
        self.assertEqual(source, "TickFlowFetcher")
        finnhub.get_daily_data.assert_not_called()
        yfinance.get_daily_data.assert_not_called()

    def test_finnhub_daily_enabled_places_finnhub_after_tickflow(self, mock_get_config):
        mock_get_config.return_value = SimpleNamespace(finnhub_daily_enabled=True)
        fetchers = self._all_failing()
        manager = DataFetcherManager(fetchers=fetchers)
        with patch.object(manager, "_longbridge_preferred", return_value=False):
            calls = self._call_order(manager, fetchers)
        self.assertEqual(
            calls,
            ["TickFlowFetcher", "FinnhubFetcher", "AlphaVantageFetcher", "YfinanceFetcher", "LongbridgeFetcher"],
        )

    def test_tickflow_priority_values_do_not_move_tickflow(self, mock_get_config):
        mock_get_config.return_value = SimpleNamespace(finnhub_daily_enabled=True)
        for tickflow_priority in (0, 1, 5, 99):
            with self.subTest(tickflow_priority=tickflow_priority):
                DataFetcherManager.reset_daily_source_health()
                fetchers = self._all_failing(TickFlowFetcher=tickflow_priority)
                manager = DataFetcherManager(fetchers=fetchers)
                with patch.object(manager, "_longbridge_preferred", return_value=False):
                    calls = self._call_order(manager, fetchers)
                self.assertEqual(calls[:2], ["TickFlowFetcher", "FinnhubFetcher"])

    def test_yfinance_priority_override_applies_after_tickflow(self, mock_get_config):
        mock_get_config.return_value = SimpleNamespace(finnhub_daily_enabled=False)
        fetchers = self._all_failing(YfinanceFetcher=0)
        manager = DataFetcherManager(fetchers=fetchers)
        with patch.object(manager, "_longbridge_preferred", return_value=False):
            calls = self._call_order(manager, fetchers)
        self.assertEqual(
            calls, ["TickFlowFetcher", "YfinanceFetcher", "AlphaVantageFetcher", "LongbridgeFetcher"]
        )

    def test_tickflow_failure_falls_back_in_order(self, mock_get_config):
        mock_get_config.return_value = SimpleNamespace(finnhub_daily_enabled=False)
        tickflow = _fetcher("TickFlowFetcher", 2, daily_error=DataFetchError("no permission"))
        alpha = _fetcher("AlphaVantageFetcher", 3, daily=_daily_df())
        yfinance = _fetcher("YfinanceFetcher", 4, daily=_daily_df())
        manager = DataFetcherManager(fetchers=[tickflow, alpha, yfinance])
        with patch.object(manager, "_longbridge_preferred", return_value=False):
            _, source = manager.get_daily_data("AAPL", start_date="2026-05-01", end_date="2026-05-08")
        self.assertEqual(source, "AlphaVantageFetcher")

    def test_longbridge_preferred_first_then_tickflow(self, mock_get_config):
        mock_get_config.return_value = SimpleNamespace(finnhub_daily_enabled=True)
        fetchers = self._all_failing(TickFlowFetcher=0, YfinanceFetcher=0)
        manager = DataFetcherManager(fetchers=fetchers)
        with patch.object(manager, "_longbridge_preferred", return_value=True):
            calls = self._call_order(manager, fetchers)
        self.assertEqual(
            calls,
            ["LongbridgeFetcher", "TickFlowFetcher", "YfinanceFetcher", "FinnhubFetcher", "AlphaVantageFetcher"],
        )

    def test_longbridge_preferred_without_finnhub_daily(self, mock_get_config):
        mock_get_config.return_value = SimpleNamespace(finnhub_daily_enabled=False)
        fetchers = self._all_failing()
        manager = DataFetcherManager(fetchers=fetchers)
        with patch.object(manager, "_longbridge_preferred", return_value=True):
            calls = self._call_order(manager, fetchers)
        self.assertEqual(
            calls, ["LongbridgeFetcher", "TickFlowFetcher", "AlphaVantageFetcher", "YfinanceFetcher"]
        )

    def test_us_index_routing_unchanged(self, mock_get_config):
        for enabled in (False, True):
            with self.subTest(finnhub_daily_enabled=enabled):
                DataFetcherManager.reset_daily_source_health()
                mock_get_config.return_value = SimpleNamespace(finnhub_daily_enabled=enabled)
                fetchers = self._all_failing()
                manager = DataFetcherManager(fetchers=fetchers)
                calls = self._call_order(manager, fetchers, code="SPX")
                self.assertEqual(calls, ["YfinanceFetcher", "FinnhubFetcher"])


class FinnhubQuoteMappingTestCase(unittest.TestCase):
    def _fetch(self, payload):
        response = MagicMock()
        response.json.return_value = payload
        response.raise_for_status.return_value = None
        with patch("src.config.get_config", return_value=SimpleNamespace(finnhub_api_key="fh-key")):
            fetcher = FinnhubFetcher()
        with patch("data_provider.finnhub_fetcher.requests.get", return_value=response), patch.object(
            FinnhubFetcher, "random_sleep", return_value=None
        ):
            return fetcher.get_realtime_quote("AAPL")

    def test_change_pct_computed_from_pc_when_dp_missing(self):
        quote = self._fetch({"c": 65.76, "pc": 92.07, "h": 70.0, "l": 64.0, "o": 69.0, "t": 1})
        self.assertEqual(quote.price, 65.76)
        self.assertEqual(quote.pre_close, 92.07)
        self.assertAlmostEqual(quote.change_pct, -28.58, places=2)
        self.assertAlmostEqual(quote.change_amount, -26.31, places=2)
        self.assertEqual(quote.source, RealtimeSource.FINNHUB)

    def test_dp_preferred_when_present(self):
        quote = self._fetch({"c": 65.76, "pc": 92.07, "dp": -28.5764, "d": -26.31})
        self.assertAlmostEqual(quote.change_pct, -28.58, places=2)

    def test_all_zero_payload_is_failure(self):
        self.assertIsNone(
            self._fetch({"c": 0, "d": None, "dp": None, "h": 0, "l": 0, "o": 0, "pc": 0, "t": 0})
        )


def _realtime_config(priority: str, **extra):
    return SimpleNamespace(
        enable_realtime_quote=True,
        realtime_source_priority="tencent,akshare_sina",
        realtime_cache_ttl=600,
        us_realtime_source_priority=priority,
        **extra,
    )


@patch("src.config.get_config")
class UsRealtimePriorityTestCase(unittest.TestCase):
    def setUp(self):
        DataFetcherManager._us_realtime_unknown_sources_warned.clear()

    def _finnhub_quote(self):
        return UnifiedRealtimeQuote(
            code="AAPL", source=RealtimeSource.FINNHUB, price=65.76, pre_close=92.07,
            change_pct=-28.58, change_amount=-26.31, high=70.0, low=64.0, open_price=69.0,
            amplitude=6.52,
        )

    def test_empty_priority_keeps_fixed_route(self, mock_get_config):
        mock_get_config.return_value = _realtime_config("")
        yfinance_quote = _quote(change_pct=1.0)
        yfinance = _fetcher("YfinanceFetcher", 4, quote=yfinance_quote)
        finnhub = _fetcher("FinnhubFetcher", 2, quote=self._finnhub_quote())
        tickflow = _fetcher("TickFlowFetcher", 2, quote=_quote(turnover_rate=0.5))
        manager = DataFetcherManager(fetchers=[finnhub, yfinance, tickflow])

        quote = manager.get_realtime_quote("AAPL")

        self.assertIs(quote, yfinance_quote)
        self.assertEqual(quote.price, 190.0)
        self.assertEqual(quote.turnover_rate, 0.5)
        # Fixed route: YFinance primary, then Finnhub -> TickFlow -> AlphaVantage supplements.
        yfinance.get_realtime_quote.assert_called_once_with("AAPL")
        finnhub.get_realtime_quote.assert_called_once_with("AAPL")
        tickflow.get_realtime_quote.assert_called_once_with("AAPL")
        self.assertIsNone(getattr(quote, "fallback_from", None))

    def test_finnhub_primary_with_tickflow_supplement(self, mock_get_config):
        mock_get_config.return_value = _realtime_config("finnhub,yfinance,tickflow")
        finnhub_quote = self._finnhub_quote()
        finnhub = _fetcher("FinnhubFetcher", 2, quote=finnhub_quote)
        yfinance = _fetcher("YfinanceFetcher", 4, quote=_quote(price=191.0, volume=1000, change_pct=9.9))
        tickflow = _fetcher("TickFlowFetcher", 2, quote=_quote(price=192.0, turnover_rate=0.42))
        manager = DataFetcherManager(fetchers=[finnhub, yfinance, tickflow])

        quote = manager.get_realtime_quote("AAPL")

        self.assertIs(quote, finnhub_quote)
        self.assertEqual(quote.source, RealtimeSource.FINNHUB)
        self.assertEqual(quote.price, 65.76)
        self.assertEqual(quote.pre_close, 92.07)
        self.assertAlmostEqual(quote.change_pct, -28.58, places=2)
        self.assertEqual(quote.volume, 1000)  # supplemented by YFinance
        self.assertEqual(quote.turnover_rate, 0.42)  # supplemented by TickFlow
        self.assertIsNone(getattr(quote, "fallback_from", None))

    def test_finnhub_failure_falls_back_to_yfinance(self, mock_get_config):
        mock_get_config.return_value = _realtime_config("finnhub,yfinance,tickflow")
        finnhub = _fetcher("FinnhubFetcher", 2, quote=None)
        yfinance_quote = _quote(price=191.0, pre_close=190.0, change_pct=0.53)
        yfinance = _fetcher("YfinanceFetcher", 4, quote=yfinance_quote)
        tickflow = _fetcher("TickFlowFetcher", 2, quote=_quote(price=192.0, turnover_rate=0.42))
        manager = DataFetcherManager(fetchers=[finnhub, yfinance, tickflow])

        quote = manager.get_realtime_quote("AAPL")

        self.assertIs(quote, yfinance_quote)
        self.assertEqual(quote.price, 191.0)
        self.assertEqual(quote.turnover_rate, 0.42)
        self.assertEqual(quote.fallback_from, "finnhub")

    def test_finnhub_all_zero_response_falls_back_to_yfinance(self, mock_get_config):
        mock_get_config.return_value = _realtime_config("finnhub,yfinance,tickflow")
        response = MagicMock()
        response.json.return_value = {"c": 0, "d": None, "dp": None, "h": 0, "l": 0, "o": 0, "pc": 0, "t": 0}
        response.raise_for_status.return_value = None
        with patch("src.config.get_config", return_value=SimpleNamespace(finnhub_api_key="fh-key")):
            finnhub = FinnhubFetcher()
        yfinance_quote = _quote(price=191.0)
        yfinance = _fetcher("YfinanceFetcher", 4, quote=yfinance_quote)
        manager = DataFetcherManager(fetchers=[finnhub, yfinance])

        with patch("data_provider.finnhub_fetcher.requests.get", return_value=response), patch.object(
            FinnhubFetcher, "random_sleep", return_value=None
        ):
            quote = manager.get_realtime_quote("AAPL")

        self.assertIs(quote, yfinance_quote)
        self.assertEqual(quote.fallback_from, "finnhub")

    def test_finnhub_quote_mapping_yields_expected_change_pct(self, mock_get_config):
        mock_get_config.return_value = _realtime_config("finnhub,yfinance")
        response = MagicMock()
        response.json.return_value = {"c": 65.76, "pc": 92.07, "h": 70.0, "l": 64.0, "o": 69.0, "t": 1}
        response.raise_for_status.return_value = None
        with patch("src.config.get_config", return_value=SimpleNamespace(finnhub_api_key="fh-key")):
            finnhub = FinnhubFetcher()
        yfinance = _fetcher("YfinanceFetcher", 4, quote=_quote(price=191.0, change_pct=3.0))
        manager = DataFetcherManager(fetchers=[finnhub, yfinance])

        with patch("data_provider.finnhub_fetcher.requests.get", return_value=response), patch.object(
            FinnhubFetcher, "random_sleep", return_value=None
        ):
            quote = manager.get_realtime_quote("AAPL")

        self.assertEqual(quote.source, RealtimeSource.FINNHUB)
        self.assertEqual(quote.price, 65.76)
        self.assertAlmostEqual(quote.change_pct, -28.58, places=2)

    def test_primary_missing_change_pct_computed_from_supplemented_pre_close(self, mock_get_config):
        mock_get_config.return_value = _realtime_config("tickflow,yfinance")
        tickflow_quote = _quote(price=110.0)
        tickflow = _fetcher("TickFlowFetcher", 2, quote=tickflow_quote)
        yfinance = _fetcher("YfinanceFetcher", 4, quote=_quote(price=999.0, pre_close=100.0, change_pct=899.0))
        manager = DataFetcherManager(fetchers=[tickflow, yfinance])

        quote = manager.get_realtime_quote("AAPL")

        self.assertEqual(quote.price, 110.0)
        self.assertEqual(quote.pre_close, 100.0)
        self.assertAlmostEqual(quote.change_pct, 10.0)

    def test_unconfigured_sources_are_skipped(self, mock_get_config):
        mock_get_config.return_value = _realtime_config("finnhub,longbridge,yfinance")
        yfinance_quote = _quote(price=191.0)
        yfinance = _fetcher("YfinanceFetcher", 4, quote=yfinance_quote)
        manager = DataFetcherManager(fetchers=[yfinance])

        quote = manager.get_realtime_quote("AAPL")

        self.assertIs(quote, yfinance_quote)
        self.assertIsNone(getattr(quote, "fallback_from", None))

    def test_all_priority_sources_fail_uses_fixed_route(self, mock_get_config):
        mock_get_config.return_value = _realtime_config("finnhub,tickflow")
        finnhub = _fetcher("FinnhubFetcher", 2, quote=None)
        tickflow = _fetcher("TickFlowFetcher", 2, quote=None)
        yfinance_quote = _quote(price=191.0)
        yfinance = _fetcher("YfinanceFetcher", 4, quote=yfinance_quote)
        manager = DataFetcherManager(fetchers=[finnhub, tickflow, yfinance])

        quote = manager.get_realtime_quote("AAPL")

        self.assertIs(quote, yfinance_quote)
        self.assertEqual(quote.fallback_from, "finnhub")

    def test_us_index_ignores_priority(self, mock_get_config):
        mock_get_config.return_value = _realtime_config("finnhub,tickflow")
        finnhub = _fetcher("FinnhubFetcher", 2, quote=self._finnhub_quote())
        tickflow = _fetcher("TickFlowFetcher", 2, quote=_quote())
        index_quote = UnifiedRealtimeQuote(code="SPX", source=RealtimeSource.FALLBACK, price=5000.0)
        yfinance = _fetcher("YfinanceFetcher", 4, quote=index_quote)
        manager = DataFetcherManager(fetchers=[finnhub, tickflow, yfinance])

        quote = manager.get_realtime_quote("SPX")

        self.assertIs(quote, index_quote)
        finnhub.get_realtime_quote.assert_not_called()
        tickflow.get_realtime_quote.assert_not_called()

    def test_unknown_source_logs_warning_and_is_ignored(self, mock_get_config):
        mock_get_config.return_value = _realtime_config("finnhub,bogus,yfinance")
        finnhub_quote = self._finnhub_quote()
        finnhub = _fetcher("FinnhubFetcher", 2, quote=finnhub_quote)
        yfinance = _fetcher("YfinanceFetcher", 4, quote=_quote(volume=10))
        manager = DataFetcherManager(fetchers=[finnhub, yfinance])

        with self.assertLogs("data_provider.base", level=logging.WARNING) as logs:
            quote = manager.get_realtime_quote("AAPL")

        self.assertIs(quote, finnhub_quote)
        self.assertTrue(any("bogus" in line for line in logs.output))

    def test_parse_priority(self, mock_get_config):
        parse = DataFetcherManager._parse_us_realtime_source_priority
        self.assertEqual(parse(""), [])
        self.assertEqual(parse(None), [])
        self.assertEqual(parse(MagicMock()), [])
        self.assertEqual(parse(" Finnhub , yfinance,finnhub,,TickFlow "), ["finnhub", "yfinance", "tickflow"])


class ConfigParsingTestCase(unittest.TestCase):
    def _load(self, env):
        from src.config import Config

        with patch.dict(os.environ, env, clear=False):
            for key in ("FINNHUB_DAILY_ENABLED", "US_REALTIME_SOURCE_PRIORITY"):
                if key not in env:
                    os.environ.pop(key, None)
            return Config._load_from_env()

    def test_defaults(self):
        config = self._load({})
        self.assertIs(config.finnhub_daily_enabled, False)
        self.assertEqual(config.us_realtime_source_priority, "")

    def test_explicit_values(self):
        config = self._load(
            {"FINNHUB_DAILY_ENABLED": "true", "US_REALTIME_SOURCE_PRIORITY": " finnhub,yfinance,tickflow "}
        )
        self.assertIs(config.finnhub_daily_enabled, True)
        self.assertEqual(config.us_realtime_source_priority, "finnhub,yfinance,tickflow")


if __name__ == "__main__":
    unittest.main()
