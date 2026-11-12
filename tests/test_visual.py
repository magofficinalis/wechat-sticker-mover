from pathlib import Path
import pytest
from PIL import Image, ImageDraw
from xhs_importer.visual import SourceMatcher, has_success_banner, VisualEvidence

FIXTURES = Path(__file__).parent / 'fixtures' / 'xhs-9.33.4'


def test_success_banner_requires_exact_picture_and_supported_geometry():
    screen = Image.new('RGB', (2560, 1440), 'white')
    banner = Image.open(FIXTURES / 'success-toast.png')
    screen.paste(banner, (1174, 704))
    assert has_success_banner(screen)
    assert not has_success_banner(Image.new('RGB', screen.size, 'black'))
    assert not has_success_banner(screen.rotate(90, expand=True))
    screen.paste('white', (1200, 720, 1280, 760))
    assert not has_success_banner(screen)


def test_matcher_checks_all_frames_and_rejects_wrong_or_blank(tmp_path):
    frames = []
    for color in ('red', 'blue'):
        im = Image.new('RGB', (120,120), 'white')
        ImageDraw.Draw(im).rectangle((20,20,100,100), fill=color)
        frames.append(im)
    path = tmp_path / 'two.gif'
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=100)
    matcher = SourceMatcher(path)
    tile = Image.new('RGB', (136,136), '#fbf8f1');tile.paste(frames[1], (8,8))
    assert matcher.matches(tile)
    assert not matcher.matches(Image.new('RGB', tile.size, 'white'))
    tile.paste('green', (28,28,109,109))
    assert not matcher.matches(tile)


def test_blank_source_fails_closed(tmp_path):
    path = tmp_path / 'blank.png';Image.new('RGB', (120,120), 'white').save(path)
    with pytest.raises(ValueError):
        SourceMatcher(path)


def test_nonsquare_image_preserves_device_rounding_before_comparison(tmp_path):
    from PIL import ImageOps
    source=Image.new('RGB',(1024,1001),'white');draw=ImageDraw.Draw(source)
    for y in range(0,1001,60):
        for x in range(0,1024,60):
            if (x//60+y//60)%2: draw.rectangle((x,y,x+59,y+59),fill='black')
    path=tmp_path/'nonsquare.png';source.save(path)
    content=ImageOps.contain(source,(120,120),Image.Resampling.LANCZOS)
    tile=Image.new('RGB',(136,136),'#fbf8f1');tile.paste(content,((136-content.width)//2,(136-content.height)//2))
    assert SourceMatcher(path).matches(tile)


def test_native_one_pixel_alignment_does_not_reject_same_image(tmp_path):
    from PIL import ImageOps
    source=Image.new('RGB',(414,396),'#ffee77');draw=ImageDraw.Draw(source)
    for y in range(0,396,12):draw.rectangle((0,y,413,y+5),fill='black')
    path=tmp_path/'aligned.png';source.save(path)
    content=ImageOps.contain(source,(120,120),Image.Resampling.LANCZOS)
    tile=Image.new('RGB',(136,136),'#fbf8f1')
    tile.paste(content,((136-content.width)//2,(136-content.height)//2+1))
    assert SourceMatcher(path).matches(tile)


def test_visual_evidence_requires_both_signals_and_new_first_tile():
    class Matcher:
        def matches(self, image): return image == 'target'
    e = VisualEvidence(Matcher())
    with pytest.raises(ValueError): e.begin(True, 'other')
    with pytest.raises(ValueError): e.begin(False, 'target')
    e.begin(False, 'other')
    e.submitted()
    assert not e.observe(True, 'other')
    assert e.observe(False, 'target')


class EvidenceMatcher:
    def matches(self, image): return image == 'target'
    def distinct_from(self, image): return image in ('other', None)


def evidence_scenario(baseline='other'):
    ticks = [10.0]
    evidence = VisualEvidence(EvidenceMatcher(), clock=lambda: ticks[0])
    evidence.begin(False, baseline)
    evidence.submitted()
    return evidence, ticks


def test_no_toast_requires_stable_changed_first_tile():
    evidence, ticks = evidence_scenario()
    assert not evidence.observe(False, 'target')
    ticks[0] += .2
    assert not evidence.observe(False, 'target')
    ticks[0] += .7
    assert evidence.observe(False, 'target')
    assert '连续' in evidence.success_note


def test_stability_resets_on_wrong_or_missing_tile():
    evidence, ticks = evidence_scenario()
    assert not evidence.observe(False, 'target')
    ticks[0] += 1
    assert not evidence.observe(False, None)
    assert not evidence.observe(False, 'target')
    ticks[0] += .9
    assert not evidence.observe(False, 'other')
    assert not evidence.observe(False, 'target')


def test_ambiguous_baseline_requires_banner_and_current_target():
    evidence, ticks = evidence_scenario('ambiguous')
    assert not evidence.observe(False, 'target')
    ticks[0] += 2
    assert not evidence.observe(False, 'target')
    assert not evidence.observe(True, 'other')
    assert evidence.observe(False, 'target')
    assert '成功提示' in evidence.success_note


def test_matching_tile_is_not_latched_after_it_disappears():
    evidence, ticks = evidence_scenario()
    assert not evidence.observe(False, 'target')
    ticks[0] += 1
    assert not evidence.observe(True, 'other')


def test_evidence_is_bounded_and_cleared_between_submissions():
    evidence, ticks = evidence_scenario()
    evidence.observe_banner(True)
    ticks[0] += 13
    assert not evidence.observe(False, 'target')
    evidence.begin(False, 'other')
    assert not evidence.observe(True, 'target')  # no real submit yet
    evidence.submitted()
    assert not evidence.observe(False, 'target')  # no stale banner


def test_timeout_note_distinguishes_missing_evidence():
    evidence, ticks = evidence_scenario()
    assert '目标缩略图' in evidence.timeout_note
    evidence.observe(True, 'other')
    assert '成功提示' in evidence.timeout_note and '目标缩略图' in evidence.timeout_note
    evidence.observe(False, 'target')
    assert evidence.success_note


def test_device_waits_for_previous_success_banner_before_new_baseline(monkeypatch):
    from xhs_importer.device import AndroidDevice
    class Matcher:
        def __init__(self, path): pass
        def matches(self, tile): return False
    monkeypatch.setattr('xhs_importer.visual.SourceMatcher',Matcher)
    monkeypatch.setattr('xhs_importer.device.time.sleep',lambda _:None)
    device=AndroidDevice(None,'test')
    observations=iter([(True,None),(False,None)])
    device.visual_observation=lambda:next(observations)
    assert device.prepare_visual('unused').armed


@pytest.mark.parametrize('changed_package', [False, True])
def test_early_banner_capture_requires_same_foreground_app(changed_package):
    from types import SimpleNamespace
    from xhs_importer.device import AndroidDevice
    from xhs_importer.xhs_adapter import PACKAGE
    screen = Image.new('RGB', (2560, 1440), 'white')
    with Image.open(FIXTURES / 'success-toast.png') as banner:
        screen.paste(banner, (1174, 704))
    device = SimpleNamespace(ui=SimpleNamespace(screenshot=lambda: screen))
    packages = iter([PACKAGE, 'other.app' if changed_package else PACKAGE])
    device.current_package = lambda: next(packages)
    evidence, ticks = evidence_scenario()
    if changed_package:
        with pytest.raises(ValueError):
            AndroidDevice.capture_visual_banner(device, evidence)
        assert not evidence.banner_seen
    else:
        AndroidDevice.capture_visual_banner(device, evidence)
        assert evidence.observe(False, 'target')


@pytest.mark.parametrize('baseline_kind', ['loading', 'blank_frame'])
def test_existing_animation_or_loading_list_cannot_claim_new_without_toast(tmp_path, baseline_kind):
    distinctive = Image.new('RGB', (120,120), 'white')
    ImageDraw.Draw(distinctive).rectangle((20,20,100,100), fill='red')
    blank = Image.new('RGB', (120,120), 'black')
    path = tmp_path / 'target.gif'
    frames = [blank, distinctive] if baseline_kind == 'blank_frame' else [distinctive]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=100)
    def tile(frame):
        result = Image.new('RGB', (136,136), '#fbf8f1')
        result.paste(frame, (8,8))
        return result
    clock = [0.0]
    e = VisualEvidence(SourceMatcher(path), clock=lambda: clock[0])
    e.begin(False, None if baseline_kind == 'loading' else tile(blank))
    e.submitted()
    assert not e.observe(False, tile(distinctive))
    clock[0] += 1
    assert not e.observe(False, tile(distinctive))
    assert e.observe(True, tile(distinctive))
