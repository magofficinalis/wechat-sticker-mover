import pytest
from PIL import Image, ImageDraw, ImageOps

from xhs_importer.visual_matcher import SourceMatcher, ManualVerificationRequired


def drawing(color='red', size=(120, 120)):
    image = Image.new('RGB', size, 'white')
    ImageDraw.Draw(image).rectangle((size[0]//6, size[1]//6,
                                    size[0]*5//6, size[1]*5//6), fill=color)
    return image


def tile_of(image, offset=(0, 0)):
    tile = Image.new('RGB', (136, 136), '#fbf8f1')
    content = ImageOps.contain(image, (120, 120), Image.Resampling.LANCZOS)
    tile.paste(content, ((136-content.width)//2+offset[0],
                        (136-content.height)//2+offset[1]))
    return tile


def test_long_animation_checks_late_frame_without_changing_source(tmp_path):
    frames = [drawing((index, 30, 70), (48, 48)) for index in range(223)]
    path = tmp_path / 'long.gif'
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=20,
                   optimize=False)
    original = path.read_bytes()
    matcher = SourceMatcher(path)
    assert matcher.frame_count == 223
    assert matcher.matches(tile_of(frames[-1]))
    assert not matcher.matches(tile_of(drawing('lime', (48, 48))))
    assert path.read_bytes() == original


def test_blank_animation_frame_does_not_reject_distinctive_frames(tmp_path):
    blank = Image.new('RGB', (120, 120), 'white')
    distinctive = drawing()
    path = tmp_path / 'blank-first.gif'
    blank.save(path, save_all=True, append_images=[distinctive], duration=100)
    matcher = SourceMatcher(path)
    assert matcher.matches(tile_of(distinctive))
    assert not matcher.matches(tile_of(blank))


def test_ambiguous_animation_frame_cannot_arm_changed_first_tile(tmp_path):
    blank = Image.new('RGB', (120, 120), 'black')
    distinctive = drawing()
    path = tmp_path / 'black-transition.gif'
    blank.save(path, save_all=True, append_images=[distinctive], duration=100)
    matcher = SourceMatcher(path)
    # The beige padding makes this uniform source appear high-contrast as a
    # whole tile. It may already be the target on its blank animation frame.
    assert not matcher.distinct_from(tile_of(blank))
    assert not matcher.distinct_from(tile_of(drawing('blue')))


def test_all_uniform_frames_still_require_manual_verification(tmp_path):
    path = tmp_path / 'uniform.gif'
    Image.new('RGB', (20, 20), 'white').save(path, save_all=True,
        append_images=[Image.new('RGB', (20, 20), 'black')], duration=100)
    with pytest.raises(ManualVerificationRequired):
        SourceMatcher(path)


@pytest.mark.parametrize('limit_name,limit', [('MAX_FRAMES', 1), ('MAX_SOURCE_PIXELS', 100)])
def test_decode_limits_fail_before_matching(tmp_path, monkeypatch, limit_name, limit):
    import xhs_importer.visual_matcher as module
    path = tmp_path / 'limited.gif'
    drawing().save(path, save_all=True, append_images=[drawing('blue')], duration=100)
    monkeypatch.setattr(module, limit_name, limit)
    with pytest.raises(ManualVerificationRequired):
        SourceMatcher(path)


def test_storage_is_bounded_and_original_aspect_controls_rendering(tmp_path):
    image = drawing('red', (1024, 1001))
    path = tmp_path / 'large.png'
    image.save(path)
    matcher = SourceMatcher(path)
    assert all(max(frame.size) <= 256 for frame in matcher.frames)
    assert matcher.matches(tile_of(image, (0, 1)))


def test_near_lookalike_is_not_a_safe_distinct_baseline(tmp_path):
    image = drawing('black')
    path = tmp_path / 'target.png'
    image.save(path)
    matcher = SourceMatcher(path)
    similar = tile_of(drawing((16, 16, 16)))
    assert not matcher.matches(similar)
    assert not matcher.distinct_from(similar)
    assert matcher.distinct_from(tile_of(drawing('blue')))
    assert not matcher.distinct_from(Image.new('RGB', (136, 136), 'white'))
    assert not matcher.distinct_from(None)


def test_small_or_invalid_baseline_is_not_distinct(tmp_path):
    path = tmp_path / 'target.png'
    drawing().save(path)
    matcher = SourceMatcher(path)
    assert not matcher.distinct_from(Image.new('RGB', (20, 20), 'black'))


def test_downsampled_thin_lines_keep_native_render_match(tmp_path):
    image = Image.new('RGB', (1024, 1001), '#ffee77')
    draw = ImageDraw.Draw(image)
    for y in range(0, image.height, 12):
        draw.rectangle((0, y, image.width - 1, y + 5), fill='black')
    path = tmp_path / 'lines.png'
    image.save(path)
    assert SourceMatcher(path).matches(tile_of(image, (0, 1)))
