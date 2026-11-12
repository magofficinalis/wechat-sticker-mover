"""Conservative, local image evidence for the observed 9.33.4 layout."""
from pathlib import Path
import time
from PIL import Image, ImageChops, ImageOps, ImageStat
from .xhs_adapter import _correlation
from .visual_matcher import SourceMatcher, ManualVerificationRequired

POLICY = 'xhs-9.33.4-visual-v1'
BANNER = (1174, 704, 1387, 783)


def has_success_banner(screen):
    if abs(screen.width / screen.height - 16/9) > .005:
        return False
    screen = screen.convert('RGB').resize((2560,1440), Image.Resampling.LANCZOS)
    crop = ImageOps.grayscale(screen.crop(BANNER))
    with Image.open(Path(__file__).parent / 'assets' / 'xhs-success-9.33.4.png') as im:
        reference = ImageOps.grayscale(im)
    return (ImageStat.Stat(ImageChops.difference(crop, reference)).mean[0] < 6
            and _correlation(crop, reference) > .98)


class VisualEvidence:
    """Fresh evidence for one confirmed submission, never shared across items."""

    def __init__(self, matcher, *, clock=time.monotonic, window=12):
        self.matcher = matcher
        self.clock, self.window = clock, window
        self.armed = False
        self._reset()

    def _reset(self):
        self.deadline = None
        self.banner_seen = False
        self.target_seen = False
        self.stable_since = None
        self.stable_count = 0
        self.baseline_distinct = False
        self.success_note = ''

    def begin(self, banner, first):
        self.armed = False
        self._reset()
        if banner:
            raise ValueError('开始前成功提示尚未消失，请稍后重试。')
        if self.matcher.matches(first):
            raise ValueError('收藏首张已经匹配目标，无法证明新增，请先核对是否重复。')
        # A loose exclusion check is intentionally stricter about the baseline
        # than the source identity check used to accept the new first tile.
        distinct = getattr(self.matcher, 'distinct_from', None)
        self.baseline_distinct = bool(distinct(first)) if distinct else False
        self.armed = True

    def submitted(self):
        if not self.armed:
            return
        self.deadline = self.clock() + self.window
        self.banner_seen = self.target_seen = False
        self.stable_since, self.stable_count = None, 0
        self.success_note = ''

    def _active(self):
        return self.armed and self.deadline is not None and self.clock() <= self.deadline

    @property
    def remaining_window(self):
        return max(0, self.deadline - self.clock()) if self.deadline is not None else 0

    def observe_banner(self, banner):
        if self._active() and banner:
            self.banner_seen = True

    def observe(self, banner, first):
        if not self._active():
            return False
        captured_at = self.clock()
        self.observe_banner(banner)
        matched = self.matcher.matches(first)
        if not self._active():
            return False
        if not matched:
            self.stable_since, self.stable_count = None, 0
            return False
        self.target_seen = True
        if self.banner_seen:
            self.success_note = '已核对本次成功提示和当前收藏首项。'
            return True
        if self.stable_since is None:
            self.stable_since = captured_at
        self.stable_count += 1
        if (self.baseline_distinct and self.stable_count >= 2
                and captured_at - self.stable_since >= .8):
            self.success_note = '收藏首项已由其他内容变为目标，连续两次核对一致。'
            return True
        return False

    @property
    def timeout_note(self):
        if not self.target_seen:
            reason = ('已看到成功提示，但未匹配到目标缩略图。' if self.banner_seen
                      else '未看到成功提示，也未匹配到目标缩略图。')
        elif not self.baseline_distinct:
            reason = '已匹配目标缩略图，但缺少成功提示，且添加前的首项无法明确区分。'
        else:
            reason = '已匹配目标缩略图，但未持续稳定，且未捕捉到成功提示。'
        return reason + '收藏结果尚未确认，请到异常处理核对。'
