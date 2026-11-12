"""Bounded, read-only image decoding. No Tk calls: safe to run in workers."""
from dataclasses import dataclass
import hashlib
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_FRAME_PIXELS = 24_000_000


@dataclass
class Preview:
    frames: list
    durations: list
    note: str = ''


def _source(path, digest):
    with Path(path).open('rb') as stream:
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError('文件超过 64 MB，暂不显示预览。')
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('原文件内容已变化，请重新扫描。')
    return BytesIO(data)


def _check_size(im):
    if im.width * im.height > MAX_FRAME_PIXELS:
        raise ValueError('图片尺寸过大，暂不显示预览。')


def render_frame(im, size):
    _check_size(im)
    content = ImageOps.exif_transpose(im).convert('RGBA')
    content = ImageOps.contain(content, (size, size), Image.Resampling.LANCZOS)
    canvas = Image.new('RGB', (size, size), '#f7f7f8')
    draw = ImageDraw.Draw(canvas)
    for y in range(0, size, 8):
        for x in range(0, size, 8):
            if (x // 8 + y // 8) % 2:
                draw.rectangle((x, y, x + 7, y + 7), fill='#e9ecf0')
    canvas.paste(content, ((size - content.width) // 2, (size - content.height) // 2), content)
    return canvas


def load_thumbnail(path, digest, size=64):
    with _source(path, digest) as data, Image.open(data) as im:
        return render_frame(im, size)


def load_preview(path, digest, *, max_frames=160, max_source_pixels=60_000_000):
    with _source(path, digest) as data, Image.open(data) as im:
        _check_size(im)
        count = getattr(im, 'n_frames', 1)
        limited = count > max_frames or count * im.width * im.height > max_source_pixels
        frames, durations = [], []
        for index in range(1 if limited else count):
            im.seek(index)
            frames.append(render_frame(im, 200))
            durations.append(max(20, min(5000, int(im.info.get('duration', 100) or 100))))
        note = '动图较大，仅显示首帧；原文件保持不变。' if limited else (
            f'循环预览 · {count} 帧' if count > 1 else '静态预览')
        return Preview(frames, durations, note)
