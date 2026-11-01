# -*- coding: utf-8 -*-
"""Outbound requests and repo metadata must not identify the upstream project."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest import mock

from src.config import Config
from src.notification_sender import GotifySender, NtfySender
from src.utils.http_identity import NEUTRAL_COMPATIBLE_USER_AGENT, NEUTRAL_USER_AGENT

REPO_ROOT = Path(__file__).resolve().parents[1]


def _ok_response():
    response = mock.MagicMock()
    response.status_code = 200
    response.text = "ok"
    return response


def test_neutral_user_agent_has_no_repository_link() -> None:
    assert NEUTRAL_USER_AGENT == "MIUSIAI/1.0"
    for agent in (NEUTRAL_USER_AGENT, NEUTRAL_COMPATIBLE_USER_AGENT):
        assert "github" not in agent.lower()
        assert "daily_stock_analysis" not in agent


@mock.patch("src.notification_sender.gotify_sender.requests.post")
def test_gotify_uses_neutral_user_agent(mock_post) -> None:
    mock_post.return_value = _ok_response()
    sender = GotifySender(Config(stock_list=[], gotify_url="https://gotify.example.com", gotify_token="t0ken"))

    assert sender.send_to_gotify("hello") is True
    assert mock_post.call_args.kwargs["headers"]["User-Agent"] == NEUTRAL_USER_AGENT


@mock.patch("src.notification_sender.ntfy_sender.requests.post")
def test_ntfy_uses_neutral_user_agent(mock_post) -> None:
    mock_post.return_value = _ok_response()
    sender = NtfySender(Config(stock_list=[], ntfy_url="https://ntfy.sh/topic"))

    assert sender.send_to_ntfy("hello") is True
    assert mock_post.call_args.kwargs["headers"]["User-Agent"] == NEUTRAL_USER_AGENT


def test_runtime_sources_do_not_send_upstream_identifiers() -> None:
    sources = [
        "data_provider/yfinance_fetcher.py",
        "src/notification_sender/gotify_sender.py",
        "src/notification_sender/ntfy_sender.py",
        "src/services/intelligence_service.py",
    ]
    for relative in sources:
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        assert "github.com/ZhuLinsen" not in text, relative
        assert '"User-Agent": "daily_stock_analysis"' not in text, relative
        assert "daily-stock-analysis-intel" not in text, relative


def test_repo_metadata_points_at_this_repository() -> None:
    assert not (REPO_ROOT / ".github/FUNDING.yml").exists()
    issue_config = (REPO_ROOT / ".github/ISSUE_TEMPLATE/config.yml").read_text(encoding="utf-8")
    assert "ZhuLinsen" not in issue_config
    assert "https://github.com/museaicode/Stock-Analysis" in issue_config


def _load_release_notes_module():
    path = REPO_ROOT / ".github/scripts/build_release_notes.py"
    spec = importlib.util.spec_from_file_location("build_release_notes_identity", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_release_notes_compare_link_uses_this_repository(monkeypatch) -> None:
    module = _load_release_notes_module()

    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    assert module._repository() == "museaicode/Stock-Analysis"
    monkeypatch.setenv("GITHUB_REPOSITORY", "someone/fork")
    assert module._repository() == "someone/fork"
    source = (REPO_ROOT / ".github/scripts/build_release_notes.py").read_text(encoding="utf-8")
    assert "ZhuLinsen" not in source
