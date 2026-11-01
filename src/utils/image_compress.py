# -*- coding: utf-8 -*-
"""
报告长图 JPEG 压缩公共函数。

供企业微信超限长图处理（src/notification_sender/wechat_sender.py）与
Web 分享图接口（api/v1/endpoints/history.py）复用。
"""
import io
import logging
import warnings
from contextlib import contextmanager
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

# JPEG 单边像素上限（libjpeg 限制 65535，Pillow 保守取 65500）
JPEG_MAX_DIMENSION = 65500
# 报告长图由本系统生成，可信；仅在 relaxed_pil_pixel_limit 内临时放宽到该上限
REPORT_IMAGE_MAX_PIXELS = 300_000_000


@contextmanager
def relaxed_pil_pixel_limit(max_pixels: int = REPORT_IMAGE_MAX_PIXELS) -> Iterator[None]:
    """临时放宽 Pillow 的 MAX_IMAGE_PIXELS 并屏蔽 DecompressionBombWarning，退出时恢复。"""
    from PIL import Image

    previous = Image.MAX_IMAGE_PIXELS
    if previous is not None and previous < max_pixels:
        Image.MAX_IMAGE_PIXELS = max_pixels
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            yield
    finally:
        Image.MAX_IMAGE_PIXELS = previous


def to_rgb(image):
    """转为 RGB；带透明通道时铺白底，避免透明区域变黑。"""
    from PIL import Image

    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    if image.mode != "RGB":
        return image.convert("RGB")
    return image


def encode_jpeg(image, quality: int, *, max_dimension: int = JPEG_MAX_DIMENSION) -> Optional[bytes]:
    """将 RGB 图片编码为 JPEG；尺寸超出 JPEG 单边上限时返回 None。"""
    width, height = image.size
    if width > max_dimension or height > max_dimension:
        return None
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue()


def compress_image_to_jpeg(image_bytes: bytes, quality: int = 85) -> Optional[bytes]:
    """
    将报告图片（通常为 PNG）整图转为 RGB JPEG。

    Returns:
        JPEG 字节；未安装 Pillow、图片无法解码或尺寸超过 JPEG 单边上限时返回 None，
        由调用方决定是否继续使用原图。
    """
    if not image_bytes:
        return None
    try:
        from PIL import Image
    except ImportError:
        logger.warning("未安装 Pillow，无法将图片压缩为 JPEG")
        return None

    try:
        with relaxed_pil_pixel_limit():
            with Image.open(io.BytesIO(image_bytes)) as opened:
                opened.load()
                rgb = to_rgb(opened)
            return encode_jpeg(rgb, quality)
    except Exception as exc:
        logger.warning("图片压缩为 JPEG 失败 (%s)", exc)
        return None
