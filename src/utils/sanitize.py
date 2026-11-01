# -*- coding: utf-8 -*-
"""Shared text sanitizers for logs, diagnostics, and API payloads."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlsplit


_REDACTED = "[REDACTED]"
_SENSITIVE_KEY_PARTS = {
    "authorization",
    "cookie",
    "password",
    "secret",
    "sendkey",
    "token",
    "webhook",
}
_SENSITIVE_KEY_PHRASES = {
    "access_token",
    "accesstoken",
    "api_key",
    "apikey",
    "api_token",
    "apitoken",
    "auth_token",
    "authtoken",
    "authorization_header",
    "authorizationheader",
    "license_key",
    "licensekey",
    "private_key",
    "privatekey",
    "refresh_token",
    "refreshtoken",
    "secret_key",
    "secretkey",
    "session_token",
    "sessiontoken",
    "send_key",
    "sendkey",
    "webhook_url",
    "webhookurl",
}
_SENSITIVE_COMPACT_KEY_PHRASES = {
    phrase.replace("_", "") for phrase in _SENSITIVE_KEY_PHRASES
}
_SENSITIVE_COMPACT_KEY_PATTERN = re.compile(
    r"authorization|cookie|password|secret|sendkey|token(?!s)|webhook"
)
_URL_PATTERN = re.compile(r"https?://[^\s,;)\]}]+", re.IGNORECASE)
_BEARER_PATTERN = re.compile(r"\b(bearer\s+)[^\s,;&]+", re.IGNORECASE)
_AUTHORIZATION_HEADER_PATTERN = re.compile(
    r"\b(authorization|proxy[_-]?authorization)(\s*[:=]\s*)"
    r"(?:(?:Bearer|Basic|Token|Digest)\s+)?[^\s,;&]+",
    re.IGNORECASE,
)
_COOKIE_HEADER_PATTERN = re.compile(
    r"\b(cookie|set[_-]?cookie)(\s*[:=]\s*)[^\s,;&]+",
    re.IGNORECASE,
)
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"\b(token|secret|password|sendkey|api[_-]?key|apikey|api[_-]?token|auth[_-]?token|"
    r"access[_-]?token|refresh[_-]?token|session[_-]?token|license[_-]?key|private[_-]?key|"
    r"secret[_-]?key|webhook[_-]?url|authorization|proxy[_-]?authorization|cookie|set[_-]?cookie)"
    r"([=:]\s*)[^\s,;&]+",
    re.IGNORECASE,
)
_TOKEN_LIKE_PATTERN = re.compile(
    r"\b(?:sk-[a-z0-9_\-]{16,}|xox[baprs]-[a-z0-9\-]{16,}|gh[pousr]_[a-z0-9_]{20,})\b",
    re.IGNORECASE,
)


def sanitize_diagnostic_text(text: Any, *, max_length: int = 300) -> str:
    """Redact common secrets and URLs from diagnostic text."""
    sanitized = str(text or "").strip()
    if not sanitized:
        return ""
    sanitized = _AUTHORIZATION_HEADER_PATTERN.sub(r"\1\2[REDACTED]", sanitized)
    sanitized = _COOKIE_HEADER_PATTERN.sub(r"\1\2[REDACTED]", sanitized)
    sanitized = _BEARER_PATTERN.sub(r"\1[REDACTED]", sanitized)
    sanitized = re.sub(r"(?i)(token|secret|password|sendkey)([=:]\s*)[^\s,;&]+", r"\1\2[REDACTED]", sanitized)
    sanitized = re.sub(r"https?://[^\s]+", "[REDACTED_URL]", sanitized)
    return " ".join(sanitized.split())[:max_length]


def redact_sensitive_mapping(obj: Any) -> Any:
    """Recursively redact sensitive values from mappings by key name only.

    This helper intentionally does not inspect arbitrary string values. P1 only
    needs a deterministic serializer for AnalysisContextPack dictionaries.
    """
    if isinstance(obj, dict):
        redacted = {}
        for key, value in obj.items():
            if _is_sensitive_mapping_key(key):
                redacted[key] = _REDACTED
            else:
                redacted[key] = redact_sensitive_mapping(value)
        return redacted
    if isinstance(obj, list):
        return [redact_sensitive_mapping(item) for item in obj]
    return obj


def sanitize_decision_signal_text(text: Any) -> str:
    """Redact obvious secrets from persisted decision-signal text without truncating."""
    sanitized = str(text or "").strip()
    if not sanitized:
        return ""
    sanitized = _URL_PATTERN.sub(_redact_sensitive_url_match, sanitized)
    sanitized = _AUTHORIZATION_HEADER_PATTERN.sub(r"\1\2[REDACTED]", sanitized)
    sanitized = _COOKIE_HEADER_PATTERN.sub(r"\1\2[REDACTED]", sanitized)
    sanitized = _BEARER_PATTERN.sub(r"\1[REDACTED]", sanitized)
    sanitized = _SECRET_ASSIGNMENT_PATTERN.sub(r"\1\2[REDACTED]", sanitized)
    sanitized = _TOKEN_LIKE_PATTERN.sub("[REDACTED]", sanitized)
    return " ".join(sanitized.split())


def sanitize_decision_signal_payload(obj: Any) -> Any:
    """Redact decision-signal JSON payloads by sensitive keys and string values."""
    redacted = redact_sensitive_mapping(obj)
    return _sanitize_decision_signal_payload_values(redacted)


def _sanitize_decision_signal_payload_values(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            key: _sanitize_decision_signal_payload_values(value)
            for key, value in obj.items()
        }
    if isinstance(obj, list):
        return [_sanitize_decision_signal_payload_values(item) for item in obj]
    if isinstance(obj, str):
        return sanitize_decision_signal_text(obj)
    return obj


def _redact_sensitive_url_match(match: re.Match[str]) -> str:
    url = match.group(0)
    if _is_sensitive_url(url):
        return "[REDACTED_URL]"
    return url


def _is_sensitive_url(url: str) -> bool:
    if _TOKEN_LIKE_PATTERN.search(url):
        return True
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    if parsed.username or parsed.password:
        return True
    if _is_webhook_url(parsed.hostname or "", parsed.path):
        return True
    return (
        _has_sensitive_url_params(parsed.query)
        or _has_sensitive_url_params(parsed.fragment)
    )


def _is_webhook_url(hostname: str, path: str) -> bool:
    hostname = str(hostname or "").lower().strip(".")
    normalized_path = f"/{path.lstrip('/').lower()}"
    path_segments = [segment for segment in normalized_path.split("/") if segment]

    if hostname == "hooks.slack.com" and normalized_path.startswith("/services/"):
        return True
    if hostname in {"discord.com", "discordapp.com"} and "/api/webhooks/" in normalized_path:
        return True
    if hostname == "open.feishu.cn" and "/open-apis/bot/" in normalized_path and "/hook/" in normalized_path:
        return True
    if hostname == "oapi.dingtalk.com" and normalized_path.startswith("/robot/send"):
        return True
    if hostname == "qyapi.weixin.qq.com" and normalized_path.startswith("/cgi-bin/webhook/send"):
        return True
    if hostname in {"sctapi.ftqq.com", "sc.ftqq.com"}:
        return True
    if hostname.startswith("hooks."):
        return True
    if {"hook", "webhook", "webhooks"} & set(path_segments):
        return True
    return False


def _has_sensitive_url_params(params_text: str) -> bool:
    if not params_text:
        return False
    try:
        params = parse_qsl(params_text, keep_blank_values=True)
    except ValueError:
        return False
    for key, value in params:
        key_text = str(key or "").strip().lower()
        if _is_sensitive_mapping_key(key_text):
            return True
        if _TOKEN_LIKE_PATTERN.search(str(value or "")):
            return True
    return False


def _is_sensitive_mapping_key(key: Any) -> bool:
    key_text = str(key or "").strip()
    if not key_text:
        return False
    parts = _mapping_key_parts(key_text)
    if _has_sensitive_phrase("_".join(parts)):
        return True
    return bool(set(parts) & _SENSITIVE_KEY_PARTS)


def _has_sensitive_phrase(normalized_key: str) -> bool:
    padded_key = f"_{normalized_key}_"
    if any(f"_{phrase}_" in padded_key for phrase in _SENSITIVE_KEY_PHRASES):
        return True
    compact_key = normalized_key.replace("_", "")
    if any(phrase in compact_key for phrase in _SENSITIVE_COMPACT_KEY_PHRASES):
        return True
    return bool(_SENSITIVE_COMPACT_KEY_PATTERN.search(compact_key))


def _mapping_key_parts(key_text: str) -> list[str]:
    split_camel = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key_text)
    return [
        part.lower()
        for part in re.split(r"[^A-Za-z0-9]+", split_camel)
        if part
    ]


# ---------------------------------------------------------------------------
# Log redaction (used by the global logging filter and notification senders)
# ---------------------------------------------------------------------------

# Telegram bot token, e.g. ``/bot123456789:AAE...`` or a bare ``123456789:AAE...``.
_TELEGRAM_BOT_TOKEN_PATTERN = re.compile(r"(?<![A-Za-z0-9])(bot)?\d{6,12}:[A-Za-z0-9_-]{30,}")
# Query/fragment parameters whose value must never reach a log line.
_LOG_SENSITIVE_QUERY_KEYS = {
    "key",
    "token",
    "access_token",
    "accesstoken",
    "sendkey",
    "send_key",
    "secret",
    "sign",
    "password",
    "passwd",
    "pwd",
    "apikey",
    "api_key",
    "api_token",
    "auth",
    "auth_token",
    "code",
    "signature",
    "sig",
    "webhook",
}
_LOG_QUERY_PARAM_PATTERN = re.compile(r"([?&#;])([^=&#\s]+)=([^&#\s]*)")
_LOG_FAST_PATH_MARKERS = (
    "://",
    "bot",
    "key",
    "token",
    "secret",
    "password",
    "bearer",
    "authorization",
    "cookie",
    "sk-",
    "send",
    "hook",
)
_LOG_FAST_PATH_TOKEN_HINT = re.compile(r"\d{6,12}:")


def _is_log_sensitive_query_key(key: str) -> bool:
    normalized = key.strip().lower()
    if normalized in _LOG_SENSITIVE_QUERY_KEYS:
        return True
    return _is_sensitive_mapping_key(normalized)


def _redact_query_params(text: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        sep, key, value = match.group(1), match.group(2), match.group(3)
        if value and _is_log_sensitive_query_key(key):
            return f"{sep}{key}={_REDACTED}"
        return match.group(0)

    return _LOG_QUERY_PARAM_PATTERN.sub(_replace, text)


def redact_sensitive_url(url: str) -> str:
    """Mask credentials inside a URL while keeping scheme and host for debugging.

    - webhook URLs (WeCom, DingTalk, Feishu/Lark hook, Slack, Discord, Server酱 …)
      keep only ``scheme://host/`` followed by ``[REDACTED]``;
    - Telegram ``/bot<token>/`` path segments are masked;
    - sensitive query/fragment parameters (key, token, access_token, sendkey …)
      have their values replaced;
    - ``user:password@`` userinfo is masked.
    """
    text = str(url or "")
    if not text:
        return text
    try:
        parsed = urlsplit(text)
    except ValueError:
        return _redact_query_params(_TELEGRAM_BOT_TOKEN_PATTERN.sub(_mask_telegram_token, text))

    hostname = parsed.hostname or ""
    if hostname and _is_webhook_url(hostname, parsed.path):
        netloc = hostname if parsed.port is None else f"{hostname}:{parsed.port}"
        return f"{parsed.scheme}://{netloc}/{_REDACTED}"

    redacted = text
    if parsed.username or parsed.password:
        netloc = hostname if parsed.port is None else f"{hostname}:{parsed.port}"
        redacted = redacted.replace(parsed.netloc, f"{_REDACTED}@{netloc}", 1)
    redacted = _TELEGRAM_BOT_TOKEN_PATTERN.sub(_mask_telegram_token, redacted)
    return _redact_query_params(redacted)


def _mask_telegram_token(match: re.Match[str]) -> str:
    return f"{match.group(1) or ''}{_REDACTED}"


def _redact_url_match_for_log(match: re.Match[str]) -> str:
    url = match.group(0)
    # Keep trailing punctuation that is very unlikely to belong to the URL.
    trailing = ""
    while url and url[-1] in ".:'\"":
        trailing = url[-1] + trailing
        url = url[:-1]
    return redact_sensitive_url(url) + trailing


def redact_log_text(text: Any) -> str:
    """Redact secrets from free-form log text without truncating it.

    Unlike :func:`sanitize_diagnostic_text`, URLs are kept (minus credentials),
    whitespace and line breaks are preserved, and nothing is truncated, so it
    is safe to apply to every log record and traceback.
    """
    if text is None:
        return ""
    sanitized = str(text)
    if not sanitized:
        return sanitized
    lowered = sanitized.lower()
    if not any(marker in lowered for marker in _LOG_FAST_PATH_MARKERS) and not _LOG_FAST_PATH_TOKEN_HINT.search(
        sanitized
    ):
        return sanitized
    sanitized = _URL_PATTERN.sub(_redact_url_match_for_log, sanitized)
    sanitized = _TELEGRAM_BOT_TOKEN_PATTERN.sub(_mask_telegram_token, sanitized)
    sanitized = _AUTHORIZATION_HEADER_PATTERN.sub(r"\1\2[REDACTED]", sanitized)
    sanitized = _COOKIE_HEADER_PATTERN.sub(r"\1\2[REDACTED]", sanitized)
    sanitized = _BEARER_PATTERN.sub(r"\1[REDACTED]", sanitized)
    sanitized = _SECRET_ASSIGNMENT_PATTERN.sub(_keep_redacted_assignment, sanitized)
    sanitized = _TOKEN_LIKE_PATTERN.sub(_REDACTED, sanitized)
    return sanitized


def _keep_redacted_assignment(match: re.Match[str]) -> str:
    return f"{match.group(1)}{match.group(2)}{_REDACTED}"
