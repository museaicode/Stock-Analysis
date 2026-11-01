# -*- coding: utf-8 -*-
"""Best-effort remote cache for the generated stock autocomplete index."""

from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Optional
from urllib.parse import urlsplit

import requests

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STOCK_INDEX_REMOTE_URL = (
    "https://raw.githubusercontent.com/museaicode/Stock-Analysis/"
    "main/apps/dsa-web/public/stocks.index.json"
)
DEFAULT_STOCK_INDEX_CACHE_PATH = REPO_ROOT / "data" / "cache" / "stocks.index.json"
DEFAULT_STOCK_INDEX_REMOTE_TTL_HOURS = 48
DEFAULT_STOCK_INDEX_REMOTE_TIMEOUT_SECONDS = 10
DEFAULT_STOCK_INDEX_REMOTE_MAX_FAILURES = 3
SUPPORTED_STOCK_INDEX_MARKETS = {"CN", "HK", "US", "BSE", "JP", "KR"}

_REMOTE_REFRESH_LOCK = Lock()
_REMOTE_FAILURE_LOCK = Lock()
_REMOTE_CONSECUTIVE_FAILURES = 0
_REMOTE_SUPPRESS_UNTIL = 0.0
_INVALID_URL_WARNED: set[str] = set()
_INVALID_URL_WARNED_LOCK = Lock()


@dataclass(frozen=True)
class RemoteStockIndexSettings:
    """Runtime settings for remote stock-index refresh."""

    enabled: bool = True
    url: str = DEFAULT_STOCK_INDEX_REMOTE_URL
    ttl_hours: int = DEFAULT_STOCK_INDEX_REMOTE_TTL_HOURS
    timeout_seconds: int = DEFAULT_STOCK_INDEX_REMOTE_TIMEOUT_SECONDS
    cache_path: Path = DEFAULT_STOCK_INDEX_CACHE_PATH


@dataclass(frozen=True)
class RemoteStockIndexResult:
    """Outcome of a best-effort refresh attempt."""

    cache_path: Optional[Path]
    refreshed: bool = False
    skipped: bool = False
    error: Optional[str] = None


def normalize_stock_index_remote_url(raw_url: Any) -> Optional[str]:
    """Return a usable http(s) URL, or ``None`` when the value is empty or invalid."""
    if not isinstance(raw_url, str):
        return None
    url = raw_url.strip()
    if not url:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return None
    return url


def _warn_invalid_remote_url_once(raw_url: Any) -> None:
    key = repr(raw_url)
    with _INVALID_URL_WARNED_LOCK:
        if key in _INVALID_URL_WARNED:
            return
        _INVALID_URL_WARNED.add(key)
    shown = raw_url.strip() if isinstance(raw_url, str) else raw_url
    logger.warning(
        "[stock-index] STOCK_INDEX_REMOTE_URL is empty or not a valid http(s) URL (%r); "
        "remote index update skipped, using local index",
        shown,
    )


def settings_from_config(config: Any) -> RemoteStockIndexSettings:
    """Build remote stock-index settings from the application config object.

    ``STOCK_INDEX_REMOTE_URL`` overrides the source. An empty or invalid value
    disables remote refresh (logged once) instead of failing startup.
    """
    enabled = bool(getattr(config, "stock_index_remote_update_enabled", True))
    raw_url = getattr(config, "stock_index_remote_url", DEFAULT_STOCK_INDEX_REMOTE_URL)
    url = normalize_stock_index_remote_url(raw_url)
    if url is None:
        if enabled:
            _warn_invalid_remote_url_once(raw_url)
        enabled = False
        url = ""
    return RemoteStockIndexSettings(
        enabled=enabled,
        url=url,
        ttl_hours=DEFAULT_STOCK_INDEX_REMOTE_TTL_HOURS,
        timeout_seconds=DEFAULT_STOCK_INDEX_REMOTE_TIMEOUT_SECONDS,
    )


def get_remote_stock_index_cache_path() -> Path:
    """Return the canonical on-disk cache path for remote stock index data."""
    return DEFAULT_STOCK_INDEX_CACHE_PATH


def is_remote_stock_index_cache_fresh(
    cache_path: Path = DEFAULT_STOCK_INDEX_CACHE_PATH,
    *,
    ttl_hours: int = DEFAULT_STOCK_INDEX_REMOTE_TTL_HOURS,
    now: Optional[float] = None,
) -> bool:
    """Return whether the remote cache exists and is still inside its TTL."""
    if ttl_hours <= 0 or not cache_path.is_file():
        return False

    current_time = time.time() if now is None else now
    try:
        age_seconds = current_time - cache_path.stat().st_mtime
    except OSError:
        return False
    return age_seconds < ttl_hours * 3600


def validate_stock_index_payload(
    payload: Any,
    *,
    min_items: int = 100,
) -> list[list[Any]]:
    """Validate the compressed ``stocks.index.json`` wire format."""
    if not isinstance(payload, list):
        raise ValueError("stock index payload must be a list")
    if len(payload) < min_items:
        raise ValueError(f"stock index payload is unexpectedly small: {len(payload)}")

    for index, item in enumerate(payload):
        if not isinstance(item, list) or len(item) < 10:
            raise ValueError(f"stock index item {index} is not a compressed tuple")

        (
            canonical_code,
            display_code,
            name,
            _pinyin,
            _abbr,
            aliases,
            market,
            asset_type,
            active,
            popularity,
        ) = item[:10]
        if not all(isinstance(value, str) and value.strip() for value in (canonical_code, display_code, name)):
            raise ValueError(f"stock index item {index} is missing code or name")
        if not isinstance(aliases, list):
            raise ValueError(f"stock index item {index} aliases must be a list")
        if market not in SUPPORTED_STOCK_INDEX_MARKETS:
            raise ValueError(f"stock index item {index} has unsupported market: {market!r}")
        if asset_type not in {"stock", "index", "etf"}:
            raise ValueError(f"stock index item {index} has unsupported asset type: {asset_type!r}")
        if not isinstance(active, bool):
            raise ValueError(f"stock index item {index} active flag must be boolean")
        if (
            isinstance(popularity, bool)
            or not isinstance(popularity, (int, float))
            or not math.isfinite(float(popularity))
        ):
            raise ValueError(f"stock index item {index} popularity must be a finite number")

    return payload


def is_valid_remote_stock_index_file(cache_path: Path = DEFAULT_STOCK_INDEX_CACHE_PATH) -> bool:
    """Return whether a cached remote stock-index file is still usable."""
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        validate_stock_index_payload(payload)
        return True
    except FileNotFoundError:
        return False
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        logger.warning("[stock-index] cached remote index is invalid: %s", exc)
        return False


def _download_remote_stock_index(settings: RemoteStockIndexSettings) -> bytes:
    response = requests.get(settings.url, timeout=settings.timeout_seconds)
    response.raise_for_status()

    content = response.content
    payload = json.loads(content.decode("utf-8"))
    validate_stock_index_payload(payload)
    return content


def _atomic_write(cache_path: Path, content: bytes) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = cache_path.with_name(f".{cache_path.name}.{os.getpid()}.tmp")
    try:
        temp_path.write_bytes(content)
        os.replace(temp_path, cache_path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def _clear_backend_stock_index_cache() -> None:
    try:
        from src.data.stock_index_loader import clear_stock_index_cache

        clear_stock_index_cache()
    except Exception as exc:  # noqa: BLE001 - cache clearing must not break refresh.
        logger.warning("[stock-index] remote index refreshed but backend cache clear failed: %s", exc)


def _reset_remote_failure_state() -> None:
    global _REMOTE_CONSECUTIVE_FAILURES, _REMOTE_SUPPRESS_UNTIL
    with _REMOTE_FAILURE_LOCK:
        _REMOTE_CONSECUTIVE_FAILURES = 0
        _REMOTE_SUPPRESS_UNTIL = 0.0


def _remote_refresh_suppressed(now: float) -> bool:
    global _REMOTE_CONSECUTIVE_FAILURES, _REMOTE_SUPPRESS_UNTIL
    with _REMOTE_FAILURE_LOCK:
        if _REMOTE_CONSECUTIVE_FAILURES < DEFAULT_STOCK_INDEX_REMOTE_MAX_FAILURES:
            return False
        if now < _REMOTE_SUPPRESS_UNTIL:
            return True
        _REMOTE_CONSECUTIVE_FAILURES = 0
        _REMOTE_SUPPRESS_UNTIL = 0.0
        return False


def _record_remote_failure(now: float, ttl_hours: int) -> int:
    global _REMOTE_CONSECUTIVE_FAILURES, _REMOTE_SUPPRESS_UNTIL
    with _REMOTE_FAILURE_LOCK:
        _REMOTE_CONSECUTIVE_FAILURES += 1
        if _REMOTE_CONSECUTIVE_FAILURES >= DEFAULT_STOCK_INDEX_REMOTE_MAX_FAILURES:
            _REMOTE_SUPPRESS_UNTIL = now + max(ttl_hours, 1) * 3600
        return _REMOTE_CONSECUTIVE_FAILURES


def refresh_remote_stock_index_cache(settings: RemoteStockIndexSettings) -> RemoteStockIndexResult:
    """Refresh the remote stock index cache without breaking callers on failure."""
    cache_path = settings.cache_path
    if not settings.enabled:
        return RemoteStockIndexResult(cache_path=cache_path if cache_path.is_file() else None, skipped=True)

    current_time = time.time()
    if is_remote_stock_index_cache_fresh(cache_path, ttl_hours=settings.ttl_hours, now=current_time):
        if is_valid_remote_stock_index_file(cache_path):
            _reset_remote_failure_state()
            return RemoteStockIndexResult(cache_path=cache_path, skipped=True)

    if _remote_refresh_suppressed(current_time):
        return RemoteStockIndexResult(
            cache_path=cache_path if is_valid_remote_stock_index_file(cache_path) else None,
            skipped=True,
            error="remote update temporarily suppressed after repeated failures",
        )

    if not _REMOTE_REFRESH_LOCK.acquire(blocking=False):
        return RemoteStockIndexResult(
            cache_path=cache_path if is_valid_remote_stock_index_file(cache_path) else None,
            skipped=True,
        )

    try:
        if is_remote_stock_index_cache_fresh(cache_path, ttl_hours=settings.ttl_hours):
            if is_valid_remote_stock_index_file(cache_path):
                _reset_remote_failure_state()
                return RemoteStockIndexResult(cache_path=cache_path, skipped=True)

        content = _download_remote_stock_index(settings)
        _atomic_write(cache_path, content)
        _clear_backend_stock_index_cache()
        _reset_remote_failure_state()
        logger.info("[stock-index] remote index refreshed: %s", cache_path)
        return RemoteStockIndexResult(cache_path=cache_path, refreshed=True)
    except Exception as exc:  # noqa: BLE001 - remote refresh is best-effort by design.
        message = str(exc)
        failures = _record_remote_failure(current_time, settings.ttl_hours)
        # Only the first failure of a streak is a warning; retries until the
        # suppression window are debug-only so serving /stocks.index.json
        # repeatedly (e.g. private repo -> 404) does not flood the log.
        log = logger.warning if failures == 1 else logger.debug
        log(
            "[stock-index] remote update failed (%d/%d), using local fallback: %s",
            failures,
            DEFAULT_STOCK_INDEX_REMOTE_MAX_FAILURES,
            message,
        )
        if is_valid_remote_stock_index_file(cache_path):
            return RemoteStockIndexResult(cache_path=cache_path, error=message)
        return RemoteStockIndexResult(cache_path=None, error=message)
    finally:
        _REMOTE_REFRESH_LOCK.release()


def _reset_remote_stock_index_state_for_tests() -> None:
    _reset_remote_failure_state()
    with _INVALID_URL_WARNED_LOCK:
        _INVALID_URL_WARNED.clear()
