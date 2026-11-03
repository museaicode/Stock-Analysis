# -*- coding: utf-8 -*-
"""Regression tests: sector ranking cache, Agent timeout fallback, failed-stock push notice."""

import os
import sys
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

from data_provider.base import DataFetcherManager
from src.analyzer import AnalysisResult
from src.core.pipeline import NotificationChannel, StockAnalysisPipeline
from src.enums import ReportType
from tests.test_pipeline_market_phase_context import _make_pipeline, _phase_payload

AGENT_TIMEOUT_ERROR = "Agent timed out after 634.31s (limit: 600.00s)"


class _RankingFetcher:
    def __init__(self, name, priority, rankings=None, error=None):
        self.name = name
        self.priority = priority
        self._rankings = rankings
        self._error = error
        self.calls = 0

    def get_sector_rankings(self, n=5):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._rankings


class SectorRankingsCacheTestCase(unittest.TestCase):
    def setUp(self):
        DataFetcherManager.clear_sector_rankings_cache_for_tests()
        self.addCleanup(DataFetcherManager.clear_sector_rankings_cache_for_tests)

    def test_success_is_shared_across_managers_within_trading_day(self):
        fetcher = _RankingFetcher(
            "AkshareFetcher",
            1,
            rankings=([{"name": "半导体", "change_pct": 3.0}], [{"name": "煤炭", "change_pct": -2.0}]),
        )
        first = DataFetcherManager(fetchers=[fetcher])
        second = DataFetcherManager(fetchers=[fetcher])

        with patch.object(DataFetcherManager, "_sector_rankings_trading_day_key", return_value="2026-11-03"):
            top1, bottom1 = first.get_sector_rankings(5)
            top2, bottom2 = second.get_sector_rankings(5)

        self.assertEqual(fetcher.calls, 1)
        self.assertEqual(top1, top2)
        self.assertEqual(bottom2[0]["name"], "煤炭")

        with patch.object(DataFetcherManager, "_sector_rankings_trading_day_key", return_value="2026-11-04"):
            first.get_sector_rankings(5)
        self.assertEqual(fetcher.calls, 2, "a new trading day must refetch")

    def test_failure_writes_negative_cache_and_later_calls_skip(self):
        fetcher = _RankingFetcher("AkshareFetcher", 1, error=TimeoutError("Read timed out. (read timeout=60)"))
        manager = DataFetcherManager(fetchers=[fetcher])

        top, bottom, chain, error = manager._get_sector_rankings_with_meta(5)
        self.assertEqual((top, bottom), ([], []))
        self.assertEqual(fetcher.calls, 1)
        self.assertIn("timed out", error)

        for _ in range(3):
            top, bottom, chain, error = DataFetcherManager(fetchers=[fetcher])._get_sector_rankings_with_meta(5)
            self.assertEqual((top, bottom), ([], []))
            self.assertTrue(error.startswith("板块数据不可用"))
            self.assertEqual(chain[0]["result"], "skipped")
        self.assertEqual(fetcher.calls, 1, "negative cache must suppress retries")

    def test_negative_cache_expires_after_ttl(self):
        fetcher = _RankingFetcher("AkshareFetcher", 1, error=RuntimeError("boom"))
        manager = DataFetcherManager(fetchers=[fetcher])
        with patch.dict(os.environ, {"SECTOR_RANKINGS_FAILURE_CACHE_MINUTES": "30"}):
            with patch("data_provider.base.time.monotonic", return_value=1000.0):
                manager._get_sector_rankings_with_meta(5)
                manager._get_sector_rankings_with_meta(5)
            self.assertEqual(fetcher.calls, 1)
            with patch("data_provider.base.time.monotonic", return_value=1000.0 + 30 * 60 + 1):
                manager._get_sector_rankings_with_meta(5)
        self.assertEqual(fetcher.calls, 2)

    def test_negative_cache_can_be_disabled(self):
        fetcher = _RankingFetcher("AkshareFetcher", 1, error=RuntimeError("boom"))
        manager = DataFetcherManager(fetchers=[fetcher])
        with patch.dict(os.environ, {"SECTOR_RANKINGS_FAILURE_CACHE_MINUTES": "0"}):
            manager._get_sector_rankings_with_meta(5)
            manager._get_sector_rankings_with_meta(5)
        self.assertEqual(fetcher.calls, 2)

    def test_boards_block_marks_unavailable_and_analysis_continues(self):
        fetcher = _RankingFetcher("AkshareFetcher", 1, error=TimeoutError("read timeout"))
        manager = DataFetcherManager(fetchers=[fetcher])
        manager._get_sector_rankings_with_meta(5)  # prime negative cache

        block = manager.get_board_context("600519", budget_seconds=5)

        self.assertEqual(fetcher.calls, 1)
        self.assertEqual(block["status"], "failed")
        self.assertTrue(any("板块数据不可用" in str(err) for err in block.get("errors", [])))


class AkshareSectorTimeoutConfigTestCase(unittest.TestCase):
    def test_default_and_env_override(self):
        from data_provider.akshare_fetcher import _get_akshare_sector_rankings_timeout

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AKSHARE_SECTOR_RANKINGS_TIMEOUT_S", None)
            self.assertEqual(_get_akshare_sector_rankings_timeout(), 15.0)
        with patch.dict(os.environ, {"AKSHARE_SECTOR_RANKINGS_TIMEOUT_S": "10"}):
            self.assertEqual(_get_akshare_sector_rankings_timeout(), 10.0)
        with patch.dict(os.environ, {"AKSHARE_SECTOR_RANKINGS_TIMEOUT_S": "abc"}):
            self.assertEqual(_get_akshare_sector_rankings_timeout(), 15.0)

    def test_sina_call_is_bounded_by_configured_timeout(self):
        from data_provider import akshare_fetcher

        fetcher = akshare_fetcher.AkshareFetcher.__new__(akshare_fetcher.AkshareFetcher)
        fetcher._set_random_user_agent = MagicMock()
        fetcher._enforce_rate_limit = MagicMock()
        calls = []

        def _fake_call(func, *args, timeout=None, call_name="", **kwargs):
            calls.append((call_name, timeout))
            raise TimeoutError(f"{call_name} 调用超过 {timeout:g}s，已放弃等待")

        with patch.dict(os.environ, {"AKSHARE_SECTOR_RANKINGS_TIMEOUT_S": "12"}), \
                patch.object(akshare_fetcher, "_akshare_call_with_timeout", side_effect=_fake_call):
            self.assertIsNone(fetcher.get_sector_rankings(5))

        self.assertEqual(
            calls,
            [("ak.stock_board_industry_name_em", 12.0), ("ak.stock_sector_spot", 12.0)],
        )


def _fallback_result(success=True, error=None):
    return AnalysisResult(
        code="600519",
        name="贵州茅台",
        sentiment_score=62,
        trend_prediction="震荡",
        operation_advice="持有",
        decision_type="hold",
        analysis_summary="常规分析结论",
        dashboard={"core_conclusion": {"one_sentence": "维持震荡判断"}},
        success=success,
        error_message=error,
    )


class AgentTimeoutFallbackTestCase(unittest.TestCase):
    def _run(self, pipeline):
        phase_context = SimpleNamespace(to_dict=MagicMock(return_value=_phase_payload()))
        with patch("src.core.pipeline.build_market_phase_context", return_value=phase_context):
            return pipeline.analyze_stock(
                "600519",
                ReportType.SIMPLE,
                "q-timeout",
                current_time=datetime(2026, 3, 27, 10, 0),
            )

    def _agent_pipeline(self, *, fallback=True, agent_error=AGENT_TIMEOUT_ERROR):
        pipeline = _make_pipeline(agent_mode=True, save_context_snapshot=False)
        pipeline.config.agent_timeout_fallback = fallback
        pipeline._analyze_with_agent = MagicMock(
            return_value=AnalysisResult(
                code="600519",
                name="贵州茅台",
                sentiment_score=50,
                trend_prediction="未知",
                operation_advice="观望",
                success=False,
                error_message=agent_error,
            )
        )
        pipeline.analyzer.analyze.return_value = _fallback_result()
        return pipeline

    def test_timeout_falls_back_to_regular_analysis_and_marks_report(self):
        pipeline = self._agent_pipeline()

        result = self._run(pipeline)

        pipeline._analyze_with_agent.assert_called_once()
        pipeline.analyzer.analyze.assert_called_once()
        self.assertTrue(result.success)
        self.assertTrue(result.analysis_summary.startswith("【本次为超时回退分析】"))
        self.assertTrue(result.get_core_conclusion().startswith("【本次为超时回退分析】"))
        self.assertIn("agent_timeout_fallback", result.data_sources)
        # Reuses already collected data instead of refetching quote / fundamentals.
        pipeline.fetcher_manager.get_fundamental_context.assert_called_once()
        saved = pipeline.db.save_analysis_history.call_args.kwargs["result"]
        self.assertIs(saved, result)

    def test_multi_agent_pipeline_timeout_also_falls_back(self):
        pipeline = self._agent_pipeline(agent_error="Pipeline timed out after 601.00s (limit: 600s)")
        result = self._run(pipeline)
        pipeline.analyzer.analyze.assert_called_once()
        self.assertTrue(result.success)

    def test_fallback_disabled_keeps_agent_failure(self):
        pipeline = self._agent_pipeline(fallback=False)
        result = self._run(pipeline)
        pipeline.analyzer.analyze.assert_not_called()
        self.assertFalse(result.success)
        self.assertEqual(result.error_message, AGENT_TIMEOUT_ERROR)

    def test_non_timeout_agent_failure_does_not_fall_back(self):
        pipeline = self._agent_pipeline(agent_error="Agent 未能生成有效的决策仪表盘")
        result = self._run(pipeline)
        pipeline.analyzer.analyze.assert_not_called()
        self.assertFalse(result.success)

    def test_fallback_failure_marks_stock_failed(self):
        pipeline = self._agent_pipeline()
        pipeline.analyzer.analyze.return_value = _fallback_result(success=False, error="LLM 返回为空")

        result = self._run(pipeline)

        self.assertFalse(result.success)
        self.assertIn("Agent 超时且回退分析失败", result.error_message)
        self.assertIn("LLM 返回为空", result.error_message)
        pipeline.db.save_analysis_history.assert_not_called()

    def test_english_report_uses_localized_note(self):
        result = _fallback_result()
        result.report_language = "en"
        StockAnalysisPipeline._mark_agent_timeout_fallback(result)
        StockAnalysisPipeline._mark_agent_timeout_fallback(result)  # idempotent
        self.assertEqual(result.analysis_summary, "[Agent timeout fallback analysis] 常规分析结论")


class _FakeNotifier:
    def __init__(self, channels, image_channels=()):
        self._markdown_to_image_channels = set(image_channels)
        self._markdown_to_image_max_chars = 15000
        self.is_available = MagicMock(return_value=True)
        self.get_available_channels = MagicMock(return_value=list(channels))
        self.get_channels_for_route = MagicMock(
            side_effect=lambda route_type, channels=None: list(channels or [])
        )
        self.send_to_context = MagicMock(return_value=False)
        self._should_use_image_for_channel = MagicMock(
            side_effect=lambda channel, image_bytes: (
                channel.value in self._markdown_to_image_channels and image_bytes is not None
            )
        )
        self._send_email_with_inline_image = MagicMock(return_value=True)
        self.send_to_email = MagicMock(return_value=True)
        self.send_to_wechat = MagicMock(return_value=True)
        self._send_wechat_report_image = MagicMock(return_value=True)
        self.generate_wechat_dashboard = MagicMock(return_value="wechat-dashboard")
        self.send = MagicMock(return_value=True)


def _ok_result(code):
    return AnalysisResult(
        code=code,
        name=f"股票{code}",
        sentiment_score=70,
        trend_prediction="看多",
        operation_advice="持有",
        analysis_summary="ok",
    )


class FailedStockNoticeTestCase(unittest.TestCase):
    FAILED = [("002410", AGENT_TIMEOUT_ERROR)]

    def _pipeline(self, notifier):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.notifier = notifier
        pipeline.config = SimpleNamespace(stock_email_groups=[], markdown_to_image_layout="card")
        pipeline._generate_aggregate_report = MagicMock(
            side_effect=lambda results, report_type: "report:" + ",".join(r.code for r in results)
        )
        return pipeline

    def test_format_notice_is_short_single_line(self):
        notice = StockAnalysisPipeline.format_failed_analyses_notice(
            [("002410", "x" * 200 + "\n第二行")]
        )
        self.assertTrue(notice.startswith("⚠️ 002410 分析失败："))
        self.assertNotIn("\n", notice)
        self.assertLessEqual(len(notice), len("⚠️ 002410 分析失败：") + 80)

    def test_text_push_appends_failed_stock_line(self):
        notifier = _FakeNotifier([NotificationChannel.EMAIL])
        pipeline = self._pipeline(notifier)

        pipeline._send_notifications(
            [_ok_result("600519")], ReportType.SIMPLE, failed_analyses=self.FAILED
        )

        sent = notifier.send_to_email.call_args.args[0]
        self.assertTrue(sent.startswith("report:600519"))
        self.assertTrue(sent.rstrip().endswith(f"⚠️ 002410 分析失败：{AGENT_TIMEOUT_ERROR}"))

    @patch("src.md2img.markdown_to_image", return_value=b"png")
    def test_image_push_renders_failed_stock_line(self, mock_md2img):
        notifier = _FakeNotifier([NotificationChannel.EMAIL], image_channels={"email"})
        pipeline = self._pipeline(notifier)

        pipeline._send_notifications(
            [_ok_result("600519")], ReportType.SIMPLE, failed_analyses=self.FAILED
        )

        notifier._send_email_with_inline_image.assert_called_once()
        rendered_markdown = mock_md2img.call_args.args[0]
        self.assertIn("⚠️ 002410 分析失败", rendered_markdown)
        # Single-stock structured poster would drop the markdown footer.
        self.assertNotIn("structured_payload", mock_md2img.call_args.kwargs)

    @patch("src.md2img.markdown_to_image", return_value=None)
    def test_wechat_dashboard_appends_failed_stock_line(self, _mock_md2img):
        notifier = _FakeNotifier([NotificationChannel.WECHAT])
        pipeline = self._pipeline(notifier)

        pipeline._send_notifications(
            [_ok_result("600519")], ReportType.SIMPLE, failed_analyses=self.FAILED
        )

        sent = notifier.send_to_wechat.call_args.args[0]
        self.assertIn("⚠️ 002410 分析失败", sent)

    def test_run_collects_failed_stock_and_passes_to_summary_push(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.max_workers = 2
        pipeline.db = MagicMock()
        pipeline.fetcher_manager = MagicMock()
        pipeline.notifier = _FakeNotifier([])
        pipeline._save_local_report = MagicMock()
        pipeline._send_notifications = MagicMock()
        pipeline.config = SimpleNamespace(
            stock_list=[],
            refresh_stock_list=lambda: None,
            single_stock_notify=False,
            report_type="simple",
            analysis_delay=0,
        )

        def _process(code, **_kwargs):
            if code == "002410":
                return AnalysisResult(
                    code=code,
                    name="明源云",
                    sentiment_score=50,
                    trend_prediction="未知",
                    operation_advice="观望",
                    success=False,
                    error_message=AGENT_TIMEOUT_ERROR,
                )
            if code == "000002":
                return None
            return _ok_result(code)

        pipeline.process_single_stock = MagicMock(side_effect=_process)

        results = pipeline.run(
            stock_codes=["600519", "002410", "000002"],
            dry_run=False,
            send_notification=True,
        )

        self.assertEqual([r.code for r in results], ["600519"])
        kwargs = pipeline._send_notifications.call_args.kwargs
        self.assertCountEqual(
            kwargs["failed_analyses"],
            [("002410", AGENT_TIMEOUT_ERROR), ("000002", "未生成分析结果，详见运行日志")],
        )
        pipeline.notifier.send.assert_not_called()

    def test_run_sends_standalone_notice_when_all_stocks_fail(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.max_workers = 1
        pipeline.db = MagicMock()
        pipeline.fetcher_manager = MagicMock()
        pipeline.notifier = _FakeNotifier([])
        pipeline._save_local_report = MagicMock()
        pipeline._send_notifications = MagicMock()
        pipeline.config = SimpleNamespace(
            stock_list=[],
            refresh_stock_list=lambda: None,
            single_stock_notify=False,
            report_type="simple",
            analysis_delay=0,
        )
        pipeline.process_single_stock = MagicMock(return_value=None)

        results = pipeline.run(stock_codes=["002410"], dry_run=False, send_notification=True)

        self.assertEqual(results, [])
        pipeline._send_notifications.assert_not_called()
        content = pipeline.notifier.send.call_args.args[0]
        self.assertIn("⚠️ 002410 分析失败", content)


if __name__ == "__main__":
    unittest.main()
