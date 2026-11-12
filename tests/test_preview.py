from PIL import Image
import pytest

from xhs_importer.catalog import file_digest
from xhs_importer.preview import load_thumbnail, load_preview


def test_thumbnail_contains_transparency_and_keeps_source(tmp_path):
    path = tmp_path / 'wide.png'
    im = Image.new('RGBA', (200, 100), (255, 0, 0, 0))
    im.paste((255, 0, 0, 255), (50, 0, 150, 100))
    im.save(path)
    before = path.read_bytes()
    thumb = load_thumbnail(path, file_digest(path))
    assert thumb.size == (64, 64)
    assert thumb.getpixel((32, 32)) == (255, 0, 0)
    assert thumb.getpixel((32, 2)) != (255, 0, 0)
    assert thumb.getpixel((2, 32)) != (0, 0, 0)
    assert path.read_bytes() == before


def test_preview_plays_frames_at_their_duration(tmp_path):
    path = tmp_path / 'two.gif'
    Image.new('RGB', (50, 40), 'red').save(path, save_all=True,
        append_images=[Image.new('RGB', (50, 40), 'blue')], duration=[80, 240], loop=0)
    result = load_preview(path, file_digest(path))
    assert len(result.frames) == 2
    assert result.durations == [80, 240]
    assert result.frames[0].getpixel((100, 100)) == (255, 0, 0)
    assert result.frames[1].getpixel((100, 100)) == (0, 0, 255)
    limited = load_preview(path, file_digest(path), max_frames=1)
    assert len(limited.frames) == 1
    assert '首帧' in limited.note
    pixel_limited = load_preview(path, file_digest(path), max_source_pixels=3000)
    assert len(pixel_limited.frames) == 1
    assert '首帧' in pixel_limited.note


def test_preview_applies_exif_orientation(tmp_path):
    path = tmp_path / 'rotate.jpg'
    im = Image.new('RGB', (80, 40), 'red')
    exif = im.getexif()
    exif[274] = 6
    im.save(path, exif=exif)
    thumb = load_thumbnail(path, file_digest(path))
    # Rotated portrait: red in upper middle, background on left middle.
    assert thumb.getpixel((32, 10))[0] > 240
    assert thumb.getpixel((4, 32))[1] > 100


def test_preview_rejects_changed_missing_or_corrupt_source(tmp_path):
    path = tmp_path / 'source.png'
    Image.new('RGB', (20, 20), 'red').save(path)
    digest = file_digest(path)
    Image.new('RGB', (20, 20), 'blue').save(path)
    with pytest.raises(ValueError, match='变化'):
        load_preview(path, digest)
    path.unlink()
    with pytest.raises(OSError):
        load_thumbnail(path, digest)
    path.write_bytes(b'broken')
    with pytest.raises(OSError):
        load_preview(path, file_digest(path))
