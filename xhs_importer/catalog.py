from dataclasses import dataclass
import hashlib
from pathlib import Path
import warnings

from PIL import Image

EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}


@dataclass(frozen=True)
class ImageItem:
    path: Path
    digest: str = ''
    format: str = ''
    width: int = 0
    height: int = 0
    frames: int = 0
    size: int = 0
    error: str = ''


def file_digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def inspect_file(source: Path, check=None) -> ImageItem:
    source = Path(source)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(source) as picture:
                fmt, width, height = picture.format, picture.width, picture.height
                frames = getattr(picture, 'n_frames', 1)
                # Read every frame; a truncated tail must not enter the queue.
                for index in range(frames):
                    if check:
                        check()
                    picture.seek(index)
                    picture.load()
        return ImageItem(source, file_digest(source), fmt, width, height, frames, source.stat().st_size)
    except (OSError, ValueError, EOFError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        return ImageItem(source, error=f'图片无法读取：{exc}')


def scan_folder(path: Path) -> list[ImageItem]:
    path = Path(path).resolve()
    if not path.is_dir():
        raise ValueError('请选择存在的图片文件夹。')
    return [inspect_file(source) for source in sorted(path.iterdir(), key=lambda p: p.name.casefold())
            if source.is_file() and source.suffix.lower() in EXTENSIONS]
