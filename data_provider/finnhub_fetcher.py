# -*- coding: utf-8 -*-
"""
FinnhubFetcher — US market data source (Priority 2)

Data source: Finnhub.io REST API
Rate limit: 60 calls/min (free tier)
Markets: US only
"""

import logging
import os
import threading
import time
from datetime import datetime
from typing import Optional

import pandas as pd
import requests

from .base import BaseFetcher, DataFetchError, STANDARD_COLUMNS
from .realtime_types import UnifiedRealtimeQuote, RealtimeSource
from .us_index_mapping import is_us_stock_code

logger = logging.getLogger(__name__)

_FINNHUB_BASE_URL = "https://finnhub.io/api/v1"

# Finnhub 免费套餐不含历史 K 线（/stock/candle 返回 403）。命中 403 后在进程内
# 记住该结论，冷却期内不再请求 candle 接口，直接交给下一个日线数据源。
_CANDLE_FORBIDDEN_COOLDOWN_SECONDS = 24 * 60 * 60


class _CandleAccessState:
    """进程级 /stock/candle 无权限状态（所有 FinnhubFetcher 实例共享）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._blocked_until: float = 0.0
        self._warned: bool = False

    def is_blocked(self, now: Optional[float] = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            return now < self._blocked_until

    def mark_forbidden(self, now: Optional[float] = None) -> bool:
        """记录 403；返回 True 表示本进程首次记录（调用方据此决定 warning/debug）。"""
        now = time.monotonic() if now is None else now
        with self._lock:
            self._blocked_until = now + _CANDLE_FORBIDDEN_COOLDOWN_SECONDS
            first = not self._warned
            self._warned = True
            return first

    def reset(self) -> None:
        with self._lock:
            self._blocked_until = 0.0
            self._warned = False


_candle_access = _CandleAccessState()


class FinnhubFetcher(BaseFetcher):
    name = "FinnhubFetcher"
    priority = 2

    def __init__(self):
        from src.config import get_config
        config = get_config()
        self._api_key = getattr(config, 'finnhub_api_key', None) or os.getenv('FINNHUB_API_KEY')
        if not self._api_key:
            logger.debug("[Finnhub] API key not configured, fetcher disabled")

    def _is_us_stock(self, stock_code: str) -> bool:
        return is_us_stock_code(stock_code)

    def is_available_for_request(self, capability: str = "") -> bool:
        """日线请求在 candle 403 冷却期内不可用；/quote 等其他能力不受影响。"""
        if capability == "daily_data" and _candle_access.is_blocked():
            return False
        return True

    @staticmethod
    def reset_candle_access_state() -> None:
        """清除 candle 403 冷却状态（测试 / 诊断用）。"""
        _candle_access.reset()

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        if not self._api_key:
            raise DataFetchError("[Finnhub] API key not configured")
        if not self._is_us_stock(stock_code):
            raise DataFetchError(f"[Finnhub] {stock_code} is not a US stock")

        symbol = stock_code.strip().upper()
        if _candle_access.is_blocked():
            logger.debug(
                "[Finnhub] /stock/candle 无权限（403）冷却中，跳过 %s 日线请求", symbol
            )
            raise DataFetchError(
                "[Finnhub] /stock/candle forbidden (403) for current plan; skipped during cooldown"
            )
        start_ts = int(datetime.strptime(start_date, '%Y-%m-%d').timestamp())
        end_ts = int(datetime.strptime(end_date, '%Y-%m-%d').timestamp())

        url = f"{_FINNHUB_BASE_URL}/stock/candle"
        params = {
            'symbol': symbol,
            'resolution': 'D',
            'from': start_ts,
            'to': end_ts,
            'token': self._api_key,
        }

        try:
            self.random_sleep(0.3, 0.8)
            resp = requests.get(url, params=params, timeout=15)
        except Exception as e:
            raise DataFetchError(f"[Finnhub] HTTP request failed for {symbol}: {e}") from e

        if getattr(resp, 'status_code', None) == 403:
            if _candle_access.mark_forbidden():
                logger.warning(
                    "[Finnhub] /stock/candle 返回 403（当前套餐不含历史 K 线），"
                    "%d 小时内不再请求该接口，美股日线交由后续数据源提供",
                    _CANDLE_FORBIDDEN_COOLDOWN_SECONDS // 3600,
                )
            else:
                logger.debug("[Finnhub] /stock/candle 再次返回 403，冷却期已刷新")
            raise DataFetchError(
                f"[Finnhub] /stock/candle forbidden (403) for {symbol}: plan has no historical candle access"
            )

        try:
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            raise DataFetchError(f"[Finnhub] HTTP request failed for {symbol}: {e}") from e

        if data.get('s') != 'ok' or not data.get('c'):
            raise DataFetchError(f"[Finnhub] No data returned for {symbol}")

        return pd.DataFrame({
            'c': data['c'],
            'h': data['h'],
            'l': data['l'],
            'o': data['o'],
            't': data['t'],
            'v': data['v'],
        })

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        if df.empty:
            return df

        df = df.copy()
        df['date'] = pd.to_datetime(df['t'], unit='s').dt.date
        df = df.rename(columns={
            'o': 'open', 'h': 'high', 'l': 'low',
            'c': 'close', 'v': 'volume',
        })
        df['pct_chg'] = df['close'].pct_change() * 100
        df['pct_chg'] = df['pct_chg'].fillna(0).round(2)
        df['amount'] = df['volume'] * df['close']
        df['code'] = stock_code

        keep = ['code'] + STANDARD_COLUMNS
        df = df[[col for col in keep if col in df.columns]]
        return df

    def get_realtime_quote(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        if not self._api_key or not self._is_us_stock(stock_code):
            return None

        symbol = stock_code.strip().upper()
        try:
            self.random_sleep(0.3, 0.8)
            resp = requests.get(
                f"{_FINNHUB_BASE_URL}/quote",
                params={'symbol': symbol, 'token': self._api_key},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"[Finnhub] Realtime quote failed for {symbol}: {e}")
            return None

        # /quote 对无效代码返回全 0（c=0），视为失败交给下一个数据源
        price = data.get('c')
        if not price or price <= 0:
            return None

        # c=当前价，pc=常规时段前收盘价，dp=涨跌幅(%)，d=涨跌额
        prev_close = data.get('pc') or None
        if prev_close is not None and prev_close <= 0:
            prev_close = None
        change_pct = data.get('dp')
        change_amount = data.get('d')
        if change_pct is None and prev_close:
            change_pct = (price - prev_close) / prev_close * 100
        if change_amount is None and prev_close:
            change_amount = price - prev_close
        high = data.get('h') or None
        low = data.get('l') or None
        open_price = data.get('o') or None

        amplitude = None
        if high and low and prev_close and prev_close > 0:
            amplitude = round((high - low) / prev_close * 100, 2)

        return UnifiedRealtimeQuote(
            code=symbol,
            source=RealtimeSource.FINNHUB,
            price=price,
            change_pct=round(change_pct, 2) if change_pct is not None else None,
            change_amount=round(change_amount, 4) if change_amount is not None else None,
            volume=data.get('v'),
            amount=None,
            volume_ratio=None,
            turnover_rate=None,
            amplitude=amplitude,
            open_price=open_price,
            high=high,
            low=low,
            pre_close=prev_close,
        )

    def get_stock_name(self, stock_code: str) -> Optional[str]:
        if not self._api_key or not self._is_us_stock(stock_code):
            return None

        symbol = stock_code.strip().upper()
        try:
            resp = requests.get(
                f"{_FINNHUB_BASE_URL}/search",
                params={'q': symbol, 'token': self._api_key},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.debug(f"[Finnhub] Symbol search failed for {symbol}: {e}")
            return None

        for item in data.get('result', []):
            if item.get('symbol') == symbol and item.get('description'):
                return item['description']
        return None
