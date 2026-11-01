# -*- coding: utf-8 -*-
"""
企业微信超限报告图片的压缩 / 切图 / 回退测试。

使用 Pillow 生成带噪点的长图模拟大文件，不依赖真实报告与转图工具。
"""
import base64
import hashlib
import io
import os
import sys
import unittest
import warnings
from unittest import mock

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import Config
from src.notification import NotificationChannel, NotificationService
from src.notification_sender import WechatSender
from src.notification_sender import wechat_sender as ws
from src.notification_sender.wechat_sender import (
    WECHAT_IMAGE_MAX_BYTES,
    WECHAT_IMAGE_MAX_SEGMENTS,
    _prepare_wechat_images,
)

WIDTH = 1080
WEBHOOK = "https://wechat.example/hook"


def _png(array: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    Image.fromarray(array).save(buffer, format="PNG")
    return buffer.getvalue()


def _noise_png(height: int, *, low: int = 0, span: int = 256, seed: int = 0, alpha: bool = False) -> bytes:
    rng = np.random.default_rng(seed)
    channels = 4 if alpha else 3
    array = (low + rng.integers(0, span, (height, WIDTH, channels))).astype(np.uint8)
    if alpha:
        array[..., 3] = 255
    return _png(array)


def _open(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


def _ok_response():
    resp = mock.MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"errcode": 0}
    return resp


def _err_response():
    resp = mock.MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"errcode": 45009, "errmsg": "api freq out of limit"}
    return resp


class _Images:
    """测试图片只生成一次。"""

    small = None        # PNG <= 2MB
    jpeg_fits = None    # PNG > 2MB，JPEG 远小于 2MB（低幅噪点）
    needs_split = None  # 满幅噪点，JPEG q65 仍 > 2MB

    @classmethod
    def load(cls):
        if cls.small is None:
            cls.small = _noise_png(300, seed=1)
            cls.jpeg_fits = _noise_png(1000, low=200, span=24, seed=2)
            cls.needs_split = _noise_png(4500, seed=3)


class TestPrepareWechatImages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _Images.load()

    def test_image_within_limit_is_returned_unchanged(self):
        data = _Images.small
        self.assertLessEqual(len(data), WECHAT_IMAGE_MAX_BYTES)
        self.assertEqual(_prepare_wechat_images(data), [data])

    def test_oversized_png_converted_to_single_jpeg(self):
        data = _Images.jpeg_fits
        self.assertGreater(len(data), WECHAT_IMAGE_MAX_BYTES)

        result = _prepare_wechat_images(data)

        self.assertIsNotNone(result)
        self.assertEqual(len(result), 1)
        self.assertLessEqual(len(result[0]), WECHAT_IMAGE_MAX_BYTES)
        image = _open(result[0])
        self.assertEqual(image.format, "JPEG")
        self.assertEqual(image.mode, "RGB")
        self.assertEqual(image.size, (WIDTH, 1000))

    def test_rgba_png_converted_to_rgb_jpeg(self):
        data = _noise_png(1000, low=200, span=24, seed=4, alpha=True)
        self.assertGreater(len(data), WECHAT_IMAGE_MAX_BYTES)

        result = _prepare_wechat_images(data)

        self.assertEqual(len(result), 1)
        self.assertEqual(_open(result[0]).mode, "RGB")

    def test_still_oversized_jpeg_split_vertically_keeping_width(self):
        data = _Images.needs_split
        source = _open(data)

        result = _prepare_wechat_images(data)

        self.assertIsNotNone(result)
        self.assertGreater(len(result), 1)
        self.assertLessEqual(len(result), WECHAT_IMAGE_MAX_SEGMENTS)
        heights = []
        for part in result:
            self.assertLessEqual(len(part), WECHAT_IMAGE_MAX_BYTES)
            image = _open(part)
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(image.width, WIDTH)
            heights.append(image.height)
        self.assertEqual(sum(heights), source.height)

        # 顺序检查：每段与原图对应区域的误差，应小于与其他区域的误差（JPEG 有损，噪点图误差较大）
        source_rgb = np.asarray(source.convert("RGB"), dtype=np.int16)
        tops = [sum(heights[:i]) for i in range(len(heights))]
        for index, part in enumerate(result):
            segment = np.asarray(_open(part), dtype=np.int16)
            own = source_rgb[tops[index]:tops[index] + heights[index]]
            other_index = (index + 1) % len(result)
            rows = min(heights[index], heights[other_index])
            other = source_rgb[tops[other_index]:tops[other_index] + rows]
            own_error = np.abs(segment - own).mean()
            other_error = np.abs(segment[:rows] - other).mean()
            self.assertLess(own_error, other_error * 0.8)

    def test_needing_more_than_max_segments_returns_none(self):
        # 将上限降到 100KB，使 1080x1500 噪点图至少需要 7 张以上
        data = _noise_png(1500, seed=5)
        with mock.patch.object(ws, "WECHAT_IMAGE_MAX_BYTES", 100 * 1024):
            self.assertIsNone(_prepare_wechat_images(data))

    def test_segments_increase_until_each_part_fits(self):
        data = _noise_png(1500, seed=6)
        # 起点估算为 ceil(jpeg_q65 / limit) + 1；限制设为 300KB 时需要多张
        with mock.patch.object(ws, "WECHAT_IMAGE_MAX_BYTES", 300 * 1024):
            result = _prepare_wechat_images(data)
        self.assertIsNotNone(result)
        self.assertGreaterEqual(len(result), 2)
        for part in result:
            self.assertLessEqual(len(part), 300 * 1024)

    def test_image_taller_than_jpeg_dimension_limit_is_split(self):
        # 低幅噪点 1080x1500：JPEG 体积很小，但高度超过（模拟的）JPEG 单边上限 1080，不能整图转 JPEG，需切分
        data = _noise_png(1500, low=200, span=24, seed=8)
        self.assertGreater(len(data), WECHAT_IMAGE_MAX_BYTES)
        with mock.patch.object(ws, "_JPEG_MAX_DIMENSION", WIDTH):
            result = _prepare_wechat_images(data)
        self.assertIsNotNone(result)
        self.assertGreaterEqual(len(result), 2)
        for part in result:
            image = _open(part)
            self.assertEqual(image.width, WIDTH)
            self.assertLessEqual(image.height, WIDTH)
            self.assertLessEqual(len(part), WECHAT_IMAGE_MAX_BYTES)

    def test_pixel_limit_relaxed_only_inside_function(self):
        data = _Images.jpeg_fits
        previous = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = 100_000  # 远小于 1080x1000，默认会触发 DecompressionBomb
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                result = _prepare_wechat_images(data)
            self.assertIsNotNone(result)
            self.assertEqual(Image.MAX_IMAGE_PIXELS, 100_000)
        finally:
            Image.MAX_IMAGE_PIXELS = previous

    def test_undecodable_oversized_bytes_returns_none(self):
        self.assertIsNone(_prepare_wechat_images(b"x" * (WECHAT_IMAGE_MAX_BYTES + 1)))

    def test_empty_bytes_returns_none(self):
        self.assertIsNone(_prepare_wechat_images(b""))


@mock.patch("src.notification_sender.wechat_sender.time.sleep")
@mock.patch("src.notification_sender.wechat_sender.requests.post")
class TestSendWechatReportImage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _Images.load()

    def _sender(self) -> WechatSender:
        return WechatSender(Config(stock_list=[], wechat_webhook_url=WEBHOOK))

    @staticmethod
    def _posted_images(mock_post):
        images = []
        for call in mock_post.call_args_list:
            payload = call.kwargs["json"]
            if payload.get("msgtype") == "image":
                raw = base64.b64decode(payload["image"]["base64"])
                images.append((raw, payload["image"]["md5"]))
        return images

    def test_small_image_sent_as_is(self, mock_post, mock_sleep):
        mock_post.return_value = _ok_response()
        sender = self._sender()

        self.assertTrue(sender._send_wechat_report_image(_Images.small, fallback_content="text"))

        posted = self._posted_images(mock_post)
        self.assertEqual(len(posted), 1)
        self.assertEqual(posted[0][0], _Images.small)
        self.assertEqual(posted[0][1], hashlib.md5(_Images.small).hexdigest())
        mock_sleep.assert_not_called()

    def test_oversized_image_sent_as_single_jpeg(self, mock_post, mock_sleep):
        mock_post.return_value = _ok_response()
        sender = self._sender()

        self.assertTrue(sender._send_wechat_report_image(_Images.jpeg_fits, fallback_content="text"))

        posted = self._posted_images(mock_post)
        self.assertEqual(len(posted), 1)
        raw, md5_hash = posted[0]
        self.assertEqual(_open(raw).format, "JPEG")
        self.assertLessEqual(len(raw), WECHAT_IMAGE_MAX_BYTES)
        self.assertEqual(md5_hash, hashlib.md5(raw).hexdigest())
        mock_sleep.assert_not_called()

    def test_split_images_sent_in_order_with_interval(self, mock_post, mock_sleep):
        mock_post.return_value = _ok_response()
        sender = self._sender()
        expected = _prepare_wechat_images(_Images.needs_split)
        self.assertGreater(len(expected), 1)

        self.assertTrue(sender._send_wechat_report_image(_Images.needs_split, fallback_content="text"))

        posted = self._posted_images(mock_post)
        self.assertEqual([raw for raw, _ in posted], expected)
        for raw, md5_hash in posted:
            self.assertLessEqual(len(raw), WECHAT_IMAGE_MAX_BYTES)
            self.assertEqual(md5_hash, hashlib.md5(raw).hexdigest())
        self.assertEqual(mock_sleep.call_count, len(expected) - 1)
        mock_sleep.assert_called_with(ws.WECHAT_IMAGE_SEND_INTERVAL_SECONDS)

    def test_too_many_segments_falls_back_to_text(self, mock_post, mock_sleep):
        sender = self._sender()
        with mock.patch.object(ws, "_prepare_wechat_images", return_value=None), \
                mock.patch.object(sender, "send_to_wechat", return_value=True) as mock_text:
            self.assertTrue(sender._send_wechat_report_image(_Images.needs_split, fallback_content="text"))
        mock_text.assert_called_once_with("text")
        self.assertEqual(self._posted_images(mock_post), [])

    def test_more_than_max_segments_real_path_falls_back_to_text(self, mock_post, mock_sleep):
        sender = self._sender()
        data = _noise_png(1500, seed=7)
        with mock.patch.object(ws, "WECHAT_IMAGE_MAX_BYTES", 100 * 1024), \
                mock.patch.object(sender, "send_to_wechat", return_value=True) as mock_text:
            self.assertTrue(sender._send_wechat_report_image(data, fallback_content="text"))
        mock_text.assert_called_once_with("text")
        self.assertEqual(self._posted_images(mock_post), [])

    def test_mid_send_failure_falls_back_to_text(self, mock_post, mock_sleep):
        mock_post.side_effect = [_ok_response(), _err_response()]
        sender = self._sender()
        with mock.patch.object(sender, "send_to_wechat", return_value=True) as mock_text:
            self.assertTrue(sender._send_wechat_report_image(_Images.needs_split, fallback_content="text"))
        self.assertEqual(len(self._posted_images(mock_post)), 2)
        mock_text.assert_called_once_with("text")

    def test_single_image_failure_falls_back_to_text(self, mock_post, mock_sleep):
        mock_post.return_value = _err_response()
        sender = self._sender()
        with mock.patch.object(sender, "send_to_wechat", return_value=False) as mock_text:
            self.assertFalse(sender._send_wechat_report_image(_Images.small, fallback_content="text"))
        mock_text.assert_called_once_with("text")


class TestNotificationServiceWechatImageRouting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _Images.load()

    def _service(self) -> NotificationService:
        cfg = Config(
            stock_list=[],
            wechat_webhook_url=WEBHOOK,
            telegram_bot_token="token",
            telegram_chat_id="chat",
            markdown_to_image_channels=["wechat", "telegram"],
        )
        with mock.patch("src.notification.get_config", return_value=cfg):
            return NotificationService()

    def _dispatch(self, service, channel, image_bytes):
        return service._send_to_static_channel(
            channel,
            "report text",
            image_bytes=image_bytes,
            email_stock_codes=None,
            email_send_to_all=False,
        )

    def test_oversized_wechat_image_goes_through_prepare_path(self):
        service = self._service()
        self.assertTrue(
            service._should_use_image_for_channel(NotificationChannel.WECHAT, _Images.needs_split)
        )
        with mock.patch.object(service, "_send_wechat_report_image", return_value=True) as mock_send, \
                mock.patch.object(service, "send_to_wechat") as mock_text:
            self.assertTrue(self._dispatch(service, NotificationChannel.WECHAT, _Images.needs_split))
        mock_send.assert_called_once_with(_Images.needs_split, fallback_content="report text")
        mock_text.assert_not_called()

    def test_wechat_without_image_still_sends_text(self):
        service = self._service()
        with mock.patch.object(service, "_send_wechat_report_image") as mock_send, \
                mock.patch.object(service, "send_to_wechat", return_value=True) as mock_text:
            self.assertTrue(self._dispatch(service, NotificationChannel.WECHAT, None))
        mock_send.assert_not_called()
        mock_text.assert_called_once_with("report text")

    def test_non_wechat_channel_receives_original_image_unprocessed(self):
        service = self._service()
        with mock.patch.object(service, "_send_telegram_photo", return_value=True) as mock_photo, \
                mock.patch.object(ws, "_prepare_wechat_images") as mock_prepare:
            self.assertTrue(self._dispatch(service, NotificationChannel.TELEGRAM, _Images.needs_split))
        mock_photo.assert_called_once_with(_Images.needs_split)
        mock_prepare.assert_not_called()


if __name__ == "__main__":
    unittest.main()
