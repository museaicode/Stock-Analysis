# -*- coding: utf-8 -*-
"""Tests: TickFlow US-stock support and Finnhub-first US daily routing."""

import os
import sys
import threading
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data_provider import tickflow_fetcher as tickflow_module
from data_provider.base import DataFetcherManager, DataFetchError
from data_provider.realtime_types import RealtimeSource
from data_provider.tickflow_fetcher import TickFlowFetcher


class _PermissionLikeError(Exception):
    def __init__(self, message="forbidden", *, status_code=403, code="FORBIDDEN"):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


class _FakeQuotes:
    def __init__(self, data=None, error=None):
        self.data = data or []
        self.error = error
        self.calls = []

    def get(self, *, symbols=None, universes=None, as_dataframe=False):
        self.calls.append({"symbols": symbols, "universes": universes})
        if self.error is not None:
            raise self.error
        return self.data


class _FakeKlines:
    def __init__(self, daily_data):
        self.daily_data = daily_data
        self.get_calls = []

    def get(self, symbol, **kwargs):
        self.get_calls.append({"symbol": symbol, **kwargs})
        return self.daily_data


class _FakeClient:
    def __init__(self, *, daily_data=None, quotes=None, quote_error=None):
        self.klines = _FakeKlines(daily_data if daily_data is not None else pd.DataFrame())
        self.quotes = _FakeQuotes(quotes, quote_error)

    def close(self):
        pass


def _daily_rows(symbol):
    # US daily bars are stamped at the 16:00 America/New_York close
    # (2024-01-02 / 2024-01-03 16:00 ET == 21:00 UTC).
    if symbol.endswith(".US"):
        stamps = (1704229200000, 1704315600000)
    else:
        stamps = (1704153600000, 1704240000000)
    return pd.DataFrame(
        [
            {"symbol": symbol, "timestamp": stamps[0], "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1000, "amount": 1000},
            {"symbol": symbol, "timestamp": stamps[1], "open": 10, "high": 12, "low": 10, "close": 11, "volume": 2000, "amount": 2500},
        ]
    )


def _quote(symbol, volume=12345):
    return {
        "symbol": symbol,
        "last_price": 190.0,
        "prev_close": 188.0,
        "open": 189.0,
        "high": 191.0,
        "low": 187.5,
        "volume": volume,
        "amount": 2345678.0,
        "timestamp": 1704153600000,
        "ext": {"name": "Apple Inc.", "change_pct": 0.0106},
    }


class TickFlowUsSymbolTestCase(unittest.TestCase):
    def test_us_symbol_conversion(self):
        cases = {
            "AAPL": "AAPL.US",
            "aapl": "AAPL.US",
            "AAPL.US": "AAPL.US",
            "BRK.B": "BRK.B.US",
            "SPX": None,
            "600519": None,
            "hk00700": None,
        }
        for code, expected in cases.items():
            with self.subTest(code=code):
                self.assertEqual(TickFlowFetcher._to_tickflow_us_symbol(code), expected)

    def test_is_us_symbol(self):
        self.assertTrue(TickFlowFetcher._is_us_symbol("AAPL.US"))
        self.assertTrue(TickFlowFetcher._is_us_symbol("brk.b.us"))
        self.assertFalse(TickFlowFetcher._is_us_symbol("600519.SH"))
        self.assertFalse(TickFlowFetcher._is_us_symbol(""))

    def test_a_share_symbol_conversion_unchanged(self):
        self.assertEqual(TickFlowFetcher._to_tickflow_symbol("600519"), "600519.SH")
        self.assertIsNone(TickFlowFetcher._to_tickflow_symbol("AAPL"))


class TickFlowUsDailyTestCase(unittest.TestCase):
    def test_us_daily_uses_us_symbol_and_keeps_share_volume(self):
        fetcher = TickFlowFetcher(api_key="sk-test")
        fetcher._client = _FakeClient(daily_data=_daily_rows("AAPL.US"))

        df = fetcher.get_daily_data("AAPL", start_date="2024-01-02", end_date="2024-01-03")

        self.assertEqual(fetcher._client.klines.get_calls[0]["symbol"], "AAPL.US")
        self.assertEqual(df["volume"].tolist(), [1000, 2000])

    def test_a_share_daily_still_converts_lots_to_shares(self):
        fetcher = TickFlowFetcher(api_key="sk-test")
        fetcher._client = _FakeClient(daily_data=_daily_rows("600519.SH"))

        df = fetcher.get_daily_data("600519", start_date="2024-01-02", end_date="2024-01-03")

        self.assertEqual(fetcher._client.klines.get_calls[0]["symbol"], "600519.SH")
        self.assertEqual(df["volume"].tolist(), [100000, 200000])

    def test_us_daily_passes_nyse_calendar(self):
        fetcher = TickFlowFetcher(api_key="sk-test")
        fetcher._client = _FakeClient(daily_data=_daily_rows("AAPL.US"))
        with patch.object(
            TickFlowFetcher, "_prepare_daily_frame", wraps=TickFlowFetcher._prepare_daily_frame
        ) as prepared:
            fetcher.get_daily_data("AAPL", start_date="2024-01-02", end_date="2024-01-03")
        self.assertEqual(prepared.call_args.kwargs["calendar"], "XNYS")

    def test_first_trading_date_uses_requested_calendar(self):
        fake_xcals = MagicMock()
        fake_xcals.get_calendar.return_value.date_to_session.return_value = pd.Timestamp("2024-01-02")
        with patch.object(tickflow_module, "xcals", fake_xcals):
            result = TickFlowFetcher._first_trading_date_on_or_after(datetime(2024, 1, 1), calendar="XNYS")
        fake_xcals.get_calendar.assert_called_once_with("XNYS")
        self.assertEqual(result, datetime(2024, 1, 2))

    def test_unsupported_symbol_raises(self):
        fetcher = TickFlowFetcher(api_key="sk-test")
        fetcher._client = _FakeClient()
        with self.assertRaises(DataFetchError) as ctx:
            fetcher._fetch_raw_data("hk00700", "2024-01-02", "2024-01-03")
        self.assertIn("US stock", str(ctx.exception))


class TickFlowUsRealtimeTestCase(unittest.TestCase):
    def test_us_realtime_keeps_share_volume(self):
        fetcher = TickFlowFetcher(api_key="sk-test")
        fetcher._client = _FakeClient(quotes=[_quote("AAPL.US", volume=12345)])

        quote = fetcher.get_realtime_quote("AAPL")

        self.assertEqual(fetcher._client.quotes.calls[0]["symbols"], ["AAPL.US"])
        self.assertEqual(quote.source, RealtimeSource.TICKFLOW)
        self.assertEqual(quote.volume, 12345)
        self.assertAlmostEqual(quote.price, 190.0)
        self.assertAlmostEqual(quote.change_pct, 1.06)
        self.assertIs(fetcher._capability_supported["us_realtime_quotes"], True)

    def test_us_realtime_quote_without_symbol_falls_back_to_us_symbol(self):
        fetcher = TickFlowFetcher(api_key="sk-test")
        raw = _quote("AAPL.US", volume=500)
        raw.pop("symbol")
        quote = fetcher._quote_to_unified_quote("AAPL", raw)
        self.assertEqual(quote.volume, 500)

    def test_us_realtime_permission_error_is_negative_cached(self):
        fetcher = TickFlowFetcher(api_key="sk-test")
        fetcher._client = _FakeClient(quote_error=_PermissionLikeError())

        self.assertIsNone(fetcher.get_realtime_quote("AAPL"))
        self.assertIsNone(fetcher.get_realtime_quote("MSFT"))

        self.assertEqual(len(fetcher._client.quotes.calls), 1)
        self.assertIs(fetcher._capability_supported["us_realtime_quotes"], False)


class _F:
    def __init__(self, name, priority):
        self.name, self.priority = name, priority


def _manager(fetchers):
    manager = DataFetcherManager.__new__(DataFetcherManager)
    manager._fetchers = list(fetchers)
    manager._fetchers_lock = threading.RLock()
    manager._fetchers_by_name = {f.name: f for f in fetchers}
    manager._fetcher_call_locks = {}
    manager._fetcher_call_locks_lock = manager._fetchers_lock
    manager._stock_name_cache = {}
    manager._stock_name_cache_lock = manager._fetchers_lock
    return manager


def _defaults(**overrides):
    priorities = {
        "FinnhubFetcher": 2,
        "TickFlowFetcher": 2,
        "AlphaVantageFetcher": 3,
        "YfinanceFetcher": 4,
        "LongbridgeFetcher": 5,
    }
    priorities.update(overrides)
    return [_F(name, priority) for name, priority in priorities.items()]


DEFAULT_ORDER = [
    "FinnhubFetcher",
    "TickFlowFetcher",
    "AlphaVantageFetcher",
    "YfinanceFetcher",
    "LongbridgeFetcher",
]
LONGBRIDGE_ORDER = [
    "LongbridgeFetcher",
    "FinnhubFetcher",
    "TickFlowFetcher",
    "AlphaVantageFetcher",
    "YfinanceFetcher",
]


class UsDailyRoutingOrderTestCase(unittest.TestCase):
    def test_builtin_source_orders(self):
        self.assertEqual(list(DataFetcherManager._US_DAILY_SOURCE_ORDER), DEFAULT_ORDER)
        self.assertEqual(
            list(DataFetcherManager._US_DAILY_SOURCE_ORDER_LONGBRIDGE_FIRST), LONGBRIDGE_ORDER
        )

    def test_default_priorities_keep_builtin_order(self):
        manager = _manager(_defaults())
        self.assertEqual(
            manager._order_us_sources_by_priority(list(DEFAULT_ORDER), pin_first=False),
            DEFAULT_ORDER,
        )

    def test_tickflow_priority_zero_still_follows_finnhub(self):
        manager = _manager(_defaults(TickFlowFetcher=0))
        self.assertEqual(
            manager._order_us_sources_by_priority(list(DEFAULT_ORDER), pin_first=False),
            DEFAULT_ORDER,
        )

    def test_yfinance_priority_zero_promotes_it_first(self):
        manager = _manager(_defaults(YfinanceFetcher=0))
        self.assertEqual(
            manager._order_us_sources_by_priority(list(DEFAULT_ORDER), pin_first=False),
            ["YfinanceFetcher", "FinnhubFetcher", "TickFlowFetcher", "AlphaVantageFetcher", "LongbridgeFetcher"],
        )

    def test_without_finnhub_tickflow_precedes_alphavantage(self):
        fetchers = [f for f in _defaults() if f.name != "FinnhubFetcher"]
        manager = _manager(fetchers)
        order = manager._order_us_sources_by_priority(list(DEFAULT_ORDER), pin_first=False)
        self.assertLess(order.index("TickFlowFetcher"), order.index("AlphaVantageFetcher"))

    def test_longbridge_preferred_stays_pinned_first(self):
        manager = _manager(_defaults())
        self.assertEqual(
            manager._order_us_sources_by_priority(list(LONGBRIDGE_ORDER), pin_first=True),
            LONGBRIDGE_ORDER,
        )

    def test_daily_market_support_includes_us(self):
        self.assertEqual(
            DataFetcherManager._DAILY_MARKET_FETCHER_SUPPORT["TickFlowFetcher"], {"cn", "us"}
        )


if __name__ == "__main__":
    unittest.main()
