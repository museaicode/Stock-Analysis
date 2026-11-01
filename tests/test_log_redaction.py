# -*- coding: utf-8 -*-
"""Secrets from notification channels must never reach log output."""

from __future__ import annotations

import io
import logging
from unittest import mock

import pytest
import requests

from src.config import Config
from src.logging_config import SensitiveDataFilter, install_sensitive_data_filter, setup_logging
from src.notification_sender import TelegramSender, WechatSender
from src.utils.sanitize import redact_log_text, redact_sensitive_url

TELEGRAM_TOKEN = "123456789:AAEhBP0av28ki9Zz1aX2bC3dE4fG5hI6jK7"
WECOM_KEY = "693a91f6-7a1b-4bc4-97a0-0ec2abcd5aaa"
WECOM_URL = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={WECOM_KEY}"


def _config(**overrides) -> Config:
    return Config(stock_list=[], **overrides)


@pytest.fixture
def filtered_stream():
    """A root handler wired exactly like setup_logging(): redaction filter on the handler."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    install_sensitive_data_filter(handler)
    root = logging.getLogger()
    previous_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield stream
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)


@pytest.mark.parametrize(
    "text, secret, expected",
    [
        (f"url: /bot{TELEGRAM_TOKEN}/sendMessage", TELEGRAM_TOKEN, "/bot[REDACTED]/sendMessage"),
        (f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto", TELEGRAM_TOKEN, "api.telegram.org"),
        (f"POST {WECOM_URL} failed", WECOM_KEY, "qyapi.weixin.qq.com"),
        (
            "https://oapi.dingtalk.com/robot/send?access_token=dt0123456789abcdef&timestamp=1&sign=abc",
            "dt0123456789abcdef",
            "oapi.dingtalk.com",
        ),
        (
            "https://open.feishu.cn/open-apis/bot/v2/hook/8a0b7c1d-aaaa-bbbb-cccc-1234567890ab",
            "8a0b7c1d-aaaa-bbbb-cccc-1234567890ab",
            "open.feishu.cn",
        ),
        ("https://sctapi.ftqq.com/SCT123456TxyzABCDEFG.send", "SCT123456TxyzABCDEFG", "sctapi.ftqq.com"),
        (
            "http://www.pushplus.plus/send?token=0123456789abcdef0123456789abcdef",
            "0123456789abcdef0123456789abcdef",
            "token=[REDACTED]",
        ),
        ("https://gotify.example.com/message?token=AbCdEf123456", "AbCdEf123456", "token=[REDACTED]"),
        ("https://example.com/api?sendkey=SK999888777", "SK999888777", "sendkey=[REDACTED]"),
        ("https://example.com/api?key=plainkey42&page=2", "plainkey42", "page=2"),
    ],
)
def test_redact_log_text_masks_channel_credentials(text: str, secret: str, expected: str) -> None:
    redacted = redact_log_text(text)

    assert secret not in redacted
    assert expected in redacted


def test_redact_log_text_keeps_ordinary_messages_untouched() -> None:
    message = "分析完成 600519 收盘价 1688.00\nnext line: https://example.com/docs?page=1"
    assert redact_log_text(message) == message


def test_redact_sensitive_url_masks_userinfo() -> None:
    assert redact_sensitive_url("https://alice:s3cret@proxy.example.com:8080/x") == (
        "https://[REDACTED]@proxy.example.com:8080/x"
    )


def test_telegram_failure_logs_do_not_contain_token(filtered_stream, caplog) -> None:
    sender = TelegramSender(_config(telegram_bot_token=TELEGRAM_TOKEN, telegram_chat_id="10001"))
    error = requests.exceptions.ConnectionError(
        "HTTPSConnectionPool(host='api.telegram.org', port=443): Max retries exceeded with url: "
        f"/bot{TELEGRAM_TOKEN}/sendMessage (Caused by NameResolutionError)"
    )

    with caplog.at_level(logging.DEBUG), \
         mock.patch("src.notification_sender.telegram_sender.requests.post", side_effect=error), \
         mock.patch("src.notification_sender.telegram_sender.time.sleep"):
        assert sender.send_to_telegram("hello") is False

    # Sender-level redaction (caplog has no filter) and the global filter output.
    assert "Telegram request failed" in caplog.text
    for text in (caplog.text, filtered_stream.getvalue()):
        assert TELEGRAM_TOKEN not in text
        assert TELEGRAM_TOKEN.split(":", 1)[1] not in text
    assert "/bot[REDACTED]/sendMessage" in caplog.text


def test_wechat_failure_logs_do_not_contain_webhook_key(filtered_stream, caplog) -> None:
    sender = WechatSender(_config(wechat_webhook_url=WECOM_URL))
    error = requests.exceptions.ConnectionError(f"Max retries exceeded with url: {WECOM_URL}")

    with caplog.at_level(logging.DEBUG), \
         mock.patch("src.notification_sender.wechat_sender.requests.post", side_effect=error):
        assert sender.send_to_wechat("hello") is False

    assert "发送企业微信消息失败" in caplog.text
    for text in (caplog.text, filtered_stream.getvalue()):
        assert WECOM_KEY not in text


def test_global_filter_redacts_args_and_tracebacks(filtered_stream) -> None:
    log = logging.getLogger("tests.redaction.child")
    try:
        raise RuntimeError(f"push failed for {WECOM_URL} via /bot{TELEGRAM_TOKEN}/sendMessage")
    except RuntimeError:
        log.exception("unexpected error calling %s", f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getMe")

    output = filtered_stream.getvalue()
    assert "unexpected error calling" in output
    assert "Traceback" in output
    assert TELEGRAM_TOKEN not in output
    assert WECOM_KEY not in output


def test_setup_logging_installs_filter_on_every_handler(tmp_path) -> None:
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    try:
        setup_logging(log_prefix="redaction_test", log_dir=str(tmp_path))
        assert root.handlers
        for handler in root.handlers:
            assert any(isinstance(f, SensitiveDataFilter) for f in handler.filters)
        logging.getLogger("tests.redaction.file").error("token leak? %s", WECOM_URL)
        for handler in root.handlers:
            handler.flush()
        log_text = "".join(p.read_text(encoding="utf-8") for p in tmp_path.glob("redaction_test_*.log"))
        assert "token leak?" in log_text
        assert WECOM_KEY not in log_text
    finally:
        for handler in list(root.handlers):
            handler.close()
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


def test_daily_analysis_artifact_excludes_logs_and_keeps_reports_briefly() -> None:
    from pathlib import Path

    import yaml

    workflow_path = Path(__file__).resolve().parents[1] / ".github/workflows/00-daily-analysis.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    upload_steps = [
        step
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if str(step.get("uses", "")).startswith("actions/upload-artifact")
    ]

    assert upload_steps
    for step in upload_steps:
        paths = [line.strip() for line in str(step["with"]["path"]).splitlines() if line.strip()]
        assert "logs/" not in paths
        assert int(step["with"]["retention-days"]) <= 3
    assert "任何人" in workflow_path.read_text(encoding="utf-8")
