# -*- coding: utf-8 -*-
"""
美股涨跌幅回归测试（INTC 财报盘后大跌场景）。

覆盖：
- YfinanceFetcher：前收盘价使用常规时段收盘价，不使用含盘后成交的 previous_close
- pipeline 一致性校验：收盘后/非交易日实时涨跌幅与日 K 线不一致时以日 K 线为准
- TickFlowFetcher：美股按 America/New_York 计算日期边界与交易日，A 股保持 Asia/Shanghai
- FinnhubFetcher：/stock/candle 403 后 24 小时内不再请求，/quote 不受影响
"""

import os
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data_provider.realtime_types import RealtimeSource, UnifiedRealtimeQuote
from data_provider.base import DataFetchError

# INTC：周四（10-29）常规时段收盘 92.07，周四盘后跌到 70.89，周五（10-30）收 65.76
REGULAR_PREV_CLOSE = 92.07
AFTER_HOURS_PRICE = 70.89
LAST_PRICE = 65.76
EXPECTED_PCT = round((LAST_PRICE - REGULAR_PREV_CLOSE) / REGULAR_PREV_CLOSE * 100, 2)  # -28.58
WRONG_PCT = -7.23  # 报告中实际显示的错误值（相对盘后价约 70.89）

NY = ZoneInfo("America/New_York")
SH = ZoneInfo("Asia/Shanghai")


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


# ---------------------------------------------------------------------------
# yfinance
# ---------------------------------------------------------------------------


class _FakeFastInfo:
    """模拟 yfinance FastInfo：previous_close 含盘后价，regular_market_previous_close 为常规时段。"""

    def __init__(self, regular_prev_close=REGULAR_PREV_CLOSE):
        self.last_price = LAST_PRICE
        self.previous_close = AFTER_HOURS_PRICE  # 由 prepost=True 的 1h K 线得到
        self.regular_market_previous_close = regular_prev_close
        self.open = 66.0
        self.day_high = 67.5
        self.day_low = 64.8
        self.last_volume = 300_000_000
        self.market_cap = None


def _fake_ticker(fast_info=None, info=None, history=None):
    ticker = MagicMock()
    ticker.fast_info = fast_info
    ticker.info = info if info is not None else {"shortName": "Intel Corporation", "currency": "USD"}
    if history is not None:
        ticker.history.return_value = history
    return ticker


class TestYfinanceRegularSessionPrevClose(unittest.TestCase):
    def _quote(self, ticker):
        from data_provider.yfinance_fetcher import YfinanceFetcher

        fake_yf = SimpleNamespace(Ticker=MagicMock(return_value=ticker))
        with patch.dict(sys.modules, {"yfinance": fake_yf}):
            return YfinanceFetcher().get_realtime_quote("INTC")

    def test_change_pct_uses_regular_session_prev_close_not_after_hours(self):
        quote = self._quote(_fake_ticker(fast_info=_FakeFastInfo()))

        self.assertIsNotNone(quote)
        self.assertEqual(quote.price, LAST_PRICE)
        self.assertEqual(quote.pre_close, REGULAR_PREV_CLOSE)
        self.assertAlmostEqual(quote.change_pct, EXPECTED_PCT, places=2)
        self.assertAlmostEqual(quote.change_pct, -28.58, places=2)
        self.assertNotAlmostEqual(quote.change_pct, WRONG_PCT, places=1)

    def test_falls_back_to_info_regular_market_previous_close(self):
        ticker = _fake_ticker(
            fast_info=_FakeFastInfo(regular_prev_close=None),
            info={
                "shortName": "Intel Corporation",
                "previousClose": AFTER_HOURS_PRICE,
                "regularMarketPreviousClose": REGULAR_PREV_CLOSE,
            },
        )
        quote = self._quote(ticker)

        self.assertEqual(quote.pre_close, REGULAR_PREV_CLOSE)
        self.assertAlmostEqual(quote.change_pct, EXPECTED_PCT, places=2)

    def test_history_fallback_excludes_pre_and_post_market(self):
        hist = pd.DataFrame(
            {
                "Open": [91.0, 66.0],
                "High": [93.0, 67.5],
                "Low": [90.5, 64.8],
                "Close": [REGULAR_PREV_CLOSE, LAST_PRICE],
                "Volume": [50_000_000, 300_000_000],
            },
            index=pd.to_datetime(["2026-10-29", "2026-10-30"]),
        )
        ticker = _fake_ticker(fast_info=None, history=hist)
        quote = self._quote(ticker)

        self.assertAlmostEqual(quote.change_pct, EXPECTED_PCT, places=2)
        _, kwargs = ticker.history.call_args
        self.assertIs(kwargs.get("prepost"), False)
        self.assertEqual(kwargs.get("interval"), "1d")


# ---------------------------------------------------------------------------
# pipeline 一致性校验
# ---------------------------------------------------------------------------


def _daily_df(source="TickFlowFetcher"):
    return pd.DataFrame(
        [
            {"date": date(2026, 10, 28), "close": 91.50, "data_source": source},
            {"date": date(2026, 10, 29), "close": REGULAR_PREV_CLOSE, "data_source": source},
            {"date": date(2026, 10, 30), "close": LAST_PRICE, "data_source": source},
        ]
    )


def _us_quote(change_pct=WRONG_PCT, pre_close=AFTER_HOURS_PRICE):
    return UnifiedRealtimeQuote(
        code="INTC",
        name="Intel",
        source=RealtimeSource.FALLBACK,
        price=LAST_PRICE,
        change_pct=change_pct,
        change_amount=round(LAST_PRICE - pre_close, 4),
        pre_close=pre_close,
        high=67.5,
        low=64.8,
    )


_CLOSED_CTX = {"phase": "non_trading", "is_partial_bar": False, "effective_daily_bar_date": "2026-10-30"}


class TestRealtimeDailyReconcile(unittest.TestCase):
    def _reconcile(self, quote, df=None, market="us", ctx=None):
        from src.core.pipeline import StockAnalysisPipeline

        return StockAnalysisPipeline._reconcile_realtime_change_with_daily(
            quote,
            _daily_df() if df is None else df,
            code="INTC",
            market=market,
            market_phase_context=_CLOSED_CTX if ctx is None else ctx,
        )

    def test_mismatch_prefers_daily_and_logs_warning(self):
        quote = _us_quote()
        with self.assertLogs("src.core.pipeline", level="WARNING") as logs:
            fixed = self._reconcile(quote)

        self.assertAlmostEqual(fixed.change_pct, -28.58, places=2)
        self.assertEqual(fixed.pre_close, REGULAR_PREV_CLOSE)
        self.assertAlmostEqual(fixed.change_amount, LAST_PRICE - REGULAR_PREV_CLOSE, places=4)
        # 入参不被修改
        self.assertEqual(quote.change_pct, WRONG_PCT)
        message = "\n".join(logs.output)
        self.assertIn("-7.23", message)
        self.assertIn("-28.58", message)
        self.assertIn("TickFlowFetcher", message)
        self.assertIn("fallback", message)

    def test_within_tolerance_keeps_realtime_value(self):
        quote = _us_quote(change_pct=-28.0, pre_close=REGULAR_PREV_CLOSE)
        self.assertIs(self._reconcile(quote), quote)

    def test_intraday_is_not_reconciled(self):
        quote = _us_quote()
        ctx = {"phase": "intraday", "is_partial_bar": True, "effective_daily_bar_date": "2026-10-29"}
        self.assertIs(self._reconcile(quote, ctx=ctx), quote)

    def test_stale_daily_bars_are_not_used(self):
        quote = _us_quote()
        ctx = dict(_CLOSED_CTX, effective_daily_bar_date="2026-11-02")
        self.assertIs(self._reconcile(quote, ctx=ctx), quote)

    def test_non_us_market_unchanged(self):
        quote = _us_quote()
        self.assertIs(self._reconcile(quote, market="cn"), quote)


class TestEnhanceContextClosedSessionDate(unittest.TestCase):
    def setUp(self):
        from src.core.pipeline import StockAnalysisPipeline

        self.pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        self.pipeline.fetcher_manager = MagicMock()
        self.pipeline.config = MagicMock()
        self.pipeline.search_service = None

    @patch("src.core.pipeline.get_market_now")
    @patch("src.core.pipeline.get_market_for_stock", return_value="us")
    def test_closed_session_labels_today_with_last_trading_day(self, _market, mock_now):
        from src.stock_analyzer import TrendAnalysisResult, TrendStatus

        # 北京时间 2026-11-01 21:28（周日）== 美东 09:28（周日）
        mock_now.return_value = datetime(2026, 11, 1, 9, 28, tzinfo=NY)
        context = {
            "code": "INTC",
            "date": "2026-10-30",
            "today": {"close": LAST_PRICE, "date": "2026-10-30"},
            "yesterday": {"close": REGULAR_PREV_CLOSE, "volume": 1},
        }
        quote = _us_quote(change_pct=EXPECTED_PCT, pre_close=REGULAR_PREV_CLOSE)
        trend = TrendAnalysisResult(code="INTC", trend_status=TrendStatus.BEAR, ma5=80.0, ma10=85.0, ma20=88.0)

        enhanced = self.pipeline._enhance_context(
            context, quote, None, trend, "Intel", market_phase_context=_CLOSED_CTX
        )

        self.assertEqual(enhanced["date"], "2026-10-30")
        self.assertEqual(enhanced["today"]["date"], "2026-10-30")
        self.assertAlmostEqual(enhanced["today"]["pct_chg"], -28.58, places=2)


# ---------------------------------------------------------------------------
# TickFlow
# ---------------------------------------------------------------------------


class _FakeKlines:
    def __init__(self, frame):
        self.frame = frame
        self.calls = []

    def get(self, symbol, **kwargs):
        self.calls.append({"symbol": symbol, **kwargs})
        return self.frame


def _tickflow(frame):
    from data_provider.tickflow_fetcher import TickFlowFetcher

    fetcher = TickFlowFetcher(api_key="sk-test")
    fetcher._client = SimpleNamespace(klines=_FakeKlines(frame), close=lambda: None)
    return fetcher


def _us_bar(day: date, close: float) -> dict:
    # 美东 16:00 收盘时间戳（2026-10-30 16:00 EDT == 北京时间 10-31 04:00）
    return {
        "symbol": "INTC.US",
        "timestamp": _ms(datetime(day.year, day.month, day.day, 16, 0, tzinfo=NY)),
        "open": close, "high": close, "low": close, "close": close,
        "volume": 1000, "amount": 1000.0,
    }


class TestTickFlowUsTradeDate(unittest.TestCase):
    def test_us_close_timestamp_maps_to_new_york_trade_date(self):
        from data_provider.tickflow_fetcher import TickFlowFetcher

        ts = _ms(datetime(2026, 10, 30, 16, 0, tzinfo=NY))
        self.assertEqual(datetime.fromtimestamp(ts / 1000, SH).date(), date(2026, 10, 31))
        raw = pd.DataFrame({"timestamp": [ts]})

        us_dates = TickFlowFetcher._extract_date_series(raw, tz_name="America/New_York")
        self.assertEqual(us_dates.iloc[0].date(), date(2026, 10, 30))

    def test_trade_date_column_takes_priority(self):
        from data_provider.tickflow_fetcher import TickFlowFetcher

        raw = pd.DataFrame(
            {
                "trade_date": ["2026-10-30"],
                "date": ["2026-10-31"],
                "timestamp": [_ms(datetime(2026, 10, 31, 4, 0, tzinfo=SH))],
            }
        )
        dates = TickFlowFetcher._extract_date_series(raw, tz_name="Asia/Shanghai")
        self.assertEqual(dates.iloc[0].date(), date(2026, 10, 30))

    def test_sunday_beijing_request_last_bar_is_friday(self):
        frame = pd.DataFrame(
            [_us_bar(date(2026, 10, 29), REGULAR_PREV_CLOSE), _us_bar(date(2026, 10, 30), LAST_PRICE)]
        )
        fetcher = _tickflow(frame)

        df = fetcher.get_daily_data("INTC", start_date="2026-10-20", end_date="2026-11-01")

        self.assertEqual(pd.Timestamp(df["date"].iloc[-1]).date(), date(2026, 10, 30))
        self.assertAlmostEqual(float(df["close"].iloc[-1]), LAST_PRICE)
        self.assertAlmostEqual(float(df["pct_chg"].iloc[-1]), -28.58, places=2)
        call = fetcher._client.klines.calls[0]
        self.assertEqual(call["symbol"], "INTC.US")
        self.assertEqual(call["start_time"], _ms(datetime(2026, 10, 20, tzinfo=NY)))
        self.assertEqual(
            call["end_time"],
            _ms(datetime(2026, 11, 2, tzinfo=NY)) - 1,
        )

    def test_a_share_keeps_shanghai_boundaries_and_dates(self):
        frame = pd.DataFrame(
            [
                {
                    "symbol": "600519.SH",
                    "timestamp": _ms(datetime(2026, 10, 30, 0, 0, tzinfo=SH)),
                    "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100, "amount": 1000,
                }
            ]
        )
        fetcher = _tickflow(frame)

        df = fetcher.get_daily_data("600519", start_date="2026-10-20", end_date="2026-10-30")

        self.assertEqual(pd.Timestamp(df["date"].iloc[-1]).date(), date(2026, 10, 30))
        call = fetcher._client.klines.calls[0]
        self.assertEqual(call["start_time"], _ms(datetime(2026, 10, 20, tzinfo=SH)))
        self.assertEqual(call["end_time"], _ms(datetime(2026, 10, 31, tzinfo=SH)) - 1)


# ---------------------------------------------------------------------------
# Finnhub
# ---------------------------------------------------------------------------


def _resp(status_code=200, payload=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload or {}
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"{status_code} Client Error: Forbidden")
    else:
        resp.raise_for_status.return_value = None
    return resp


class TestFinnhubCandleForbiddenCache(unittest.TestCase):
    def setUp(self):
        from data_provider.finnhub_fetcher import FinnhubFetcher

        FinnhubFetcher.reset_candle_access_state()
        self.fetcher = FinnhubFetcher()
        self.fetcher._api_key = "test-key"
        self.fetcher.random_sleep = lambda *a, **k: None

    def tearDown(self):
        from data_provider.finnhub_fetcher import FinnhubFetcher

        FinnhubFetcher.reset_candle_access_state()

    @staticmethod
    def _candle_calls(mock_get):
        return [c for c in mock_get.call_args_list if "/stock/candle" in c.args[0]]

    def test_403_skips_candle_for_24_hours_and_quote_still_works(self):
        import data_provider.finnhub_fetcher as fh

        quote_payload = {"c": LAST_PRICE, "pc": REGULAR_PREV_CLOSE, "dp": -28.58, "d": -26.31,
                         "h": 67.5, "l": 64.8, "o": 66.0}

        def fake_get(url, params=None, timeout=None):
            if url.endswith("/stock/candle"):
                return _resp(403)
            return _resp(200, quote_payload)

        clock = [1000.0]
        with patch.object(fh.requests, "get", side_effect=fake_get) as mock_get, \
                patch.object(fh.time, "monotonic", side_effect=lambda: clock[0]):
            with self.assertLogs("data_provider.finnhub_fetcher", level="DEBUG") as logs:
                with self.assertRaises(DataFetchError):
                    self.fetcher._fetch_raw_data("INTC", "2026-10-01", "2026-10-30")
                self.assertFalse(self.fetcher.is_available_for_request("daily_data"))
                self.assertTrue(self.fetcher.is_available_for_request("realtime_quote"))

                clock[0] += 23 * 3600
                with self.assertRaises(DataFetchError):
                    self.fetcher._fetch_raw_data("INTC", "2026-10-01", "2026-10-30")
                self.assertEqual(len(self._candle_calls(mock_get)), 1)

                quote = self.fetcher.get_realtime_quote("INTC")
                self.assertIsNotNone(quote)
                self.assertAlmostEqual(quote.change_pct, -28.58, places=2)

                clock[0] += 2 * 3600  # 超过 24 小时，允许重新探测
                self.assertTrue(self.fetcher.is_available_for_request("daily_data"))
                with self.assertRaises(DataFetchError):
                    self.fetcher._fetch_raw_data("INTC", "2026-10-01", "2026-10-30")
                self.assertEqual(len(self._candle_calls(mock_get)), 2)

        warnings = [r for r in logs.records if r.levelname == "WARNING"]
        self.assertEqual(len(warnings), 1)
        self.assertIn("403", warnings[0].getMessage())

    def test_manager_filter_skips_finnhub_daily_after_403(self):
        from data_provider.base import DataFetcherManager
        import data_provider.finnhub_fetcher as fh

        with patch.object(fh.requests, "get", return_value=_resp(403)):
            with self.assertRaises(DataFetchError):
                self.fetcher._fetch_raw_data("INTC", "2026-10-01", "2026-10-30")

        kept = DataFetcherManager._filter_fetchers_by_capability([self.fetcher], capability="daily_data")
        self.assertEqual(kept, [])
        kept_rt = DataFetcherManager._filter_fetchers_by_capability([self.fetcher], capability="realtime_quote")
        self.assertEqual(kept_rt, [self.fetcher])


if __name__ == "__main__":
    unittest.main()
