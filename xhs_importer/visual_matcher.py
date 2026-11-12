"""Bounded local thumbnail comparison; source files are never rewritten."""
from math import isqrt
from statistics import median

from PIL import Image, ImageChops, ImageOps, ImageStat

from .xhs_adapter import _correlation

MAX_FRAMES = 1000
MAX_SOURCE_PIXELS = 300_000_000
MAX_STORED_BYTES = 96 * 1024 * 1024
RATIOS = (118 / 136, 120 / 136, 122 / 136)
OFFSETS = ((0, 1), (0, -1), (1, 0), (-1, 0),
           (1, 1), (-1, 1), (1, -1), (-1, -1))


class ManualVerificationRequired(ValueError):
    """A source cannot be matched reliably; explicit manual mode may proceed."""


def _contained_size(source_size, extent):
    """Use original aspect/rounding even when the stored frame is smaller."""
    width, height = source_size
    if width / height > extent[0] / extent[1]:
        return extent[0], max(1, round(height / width * extent[0]))
    return max(1, round(width / height * extent[1])), extent[1]


def _error(left, right):
    return sum(ImageStat.Stat(ImageChops.difference(left, right)).mean) / 3


class SourceMatcher:
    def __init__(self, path):
        self.frames = []
        self._sizes = []
        self._cache_key = None
        self._templates = []
        self._ambiguous_frames = False
        with Image.open(path) as source:
            self.frame_count = getattr(source, 'n_frames', 1)
            if (self.frame_count > MAX_FRAMES or
                    source.width * source.height * self.frame_count > MAX_SOURCE_PIXELS):
                raise ManualVerificationRequired(
                    '图片帧数或尺寸超过自动核对范围。请确认未收藏后重新排队，使用逐张人工核对。')
            # At most 96 MiB of retained RGBA pixels, including 1000-frame GIFs.
            side = min(256, max(1, isqrt(MAX_STORED_BYTES // (4 * self.frame_count))))
            for index in range(self.frame_count):
                source.seek(index)
                frame = ImageOps.exif_transpose(source).convert('RGBA')
                original_size = frame.size
                frame.thumbnail((side, side), Image.Resampling.LANCZOS, reducing_gap=None)
                rendered = Image.new('RGBA', frame.size, 'white')
                rendered.alpha_composite(frame)
                # A blank transition frame is not an identity signal, but it
                # does not invalidate the other frames of an animation.
                if max(ImageStat.Stat(rendered.convert('RGB')).stddev) < 10:
                    self._ambiguous_frames = True
                    continue
                self.frames.append(frame)
                self._sizes.append(original_size)
        if not self.frames:
            raise ManualVerificationRequired(
                '图片过于单一，不能可靠自动核对。请确认未收藏后重新排队，使用逐张人工核对。')

    def _content(self, index, ratio, size):
        extent = tuple(round(d * ratio) for d in size)
        content_size = _contained_size(self._sizes[index], extent)
        return self.frames[index].resize(content_size, Image.Resampling.LANCZOS)

    def _render(self, index, ratio, size, background, offset=(0, 0), content=None):
        if content is None:
            content = self._content(index, ratio, size)
        expected = Image.new('RGBA', size, background + (255,))
        expected.alpha_composite(content, (
            (size[0] - content.width) // 2 + offset[0],
            (size[1] - content.height) // 2 + offset[1]))
        return expected.convert('RGB').resize((68, 68), Image.Resampling.LANCZOS)

    @staticmethod
    def _usable(tile):
        return tile is not None and 40 <= min(tile.size) and max(tile.size) <= 512

    def _candidates(self, size, background):
        key = size, background
        if key != self._cache_key:
            # One size/background only: <= 45 MiB for all 1000 frames.
            self._templates = []
            for index in range(len(self.frames)):
                for ratio in RATIOS:
                    center = self._render(index, ratio, size, background)
                    coarse = center.resize((16, 16), Image.Resampling.BOX)
                    self._templates.append((index, ratio, center, coarse))
            self._cache_key = key
        return self._templates

    def _matches(self, tile, error_limit, correlation_limit, prune):
        if not self._usable(tile):
            return False
        tile = tile.convert('RGB')
        size = tile.size
        corners = [tile.getpixel(p) for p in (
            (0, 0), (tile.width - 1, 0), (0, tile.height - 1),
            (tile.width - 1, tile.height - 1))]
        background = tuple(int(median(c[i] for c in corners)) for i in range(3))
        tile = tile.resize((68, 68), Image.Resampling.LANCZOS)
        gray_tile = ImageOps.grayscale(tile)
        coarse_tile = tile.resize((16, 16), Image.Resampling.BOX)
        candidates = []
        for index, ratio, center, coarse in self._candidates(size, background):
            # This speed filter is used only for positive identification. A
            # rejected candidate cannot be interpreted as a distinct baseline.
            if prune and _error(coarse_tile, coarse) >= 12:
                continue
            candidates.append((_error(tile, center), index, ratio, center))
        candidates.sort(key=lambda item: item[0])
        for error, index, ratio, center in candidates:
            if error < error_limit and _correlation(gray_tile, ImageOps.grayscale(center)) > correlation_limit:
                return True
            content = self._content(index, ratio, size)
            for offset in OFFSETS:
                expected = self._render(index, ratio, size, background, offset, content)
                if (_error(tile, expected) < error_limit and
                        _correlation(gray_tile, ImageOps.grayscale(expected)) > correlation_limit):
                    return True
        return False

    def matches(self, tile):
        return self._matches(tile, 4.5, .94, prune=True)

    def distinct_from(self, tile):
        # A padded black/white transition frame can look high-contrast as a
        # whole tile. Because those frames cannot establish identity, they
        # cannot establish a definitely different pre-import image either.
        if self._ambiguous_frames:
            return False
        if tile is None:
            # Missing children can also mean the existing list is loading.
            # Only an observed, distinctive different image can arm fallback.
            return False
        if not self._usable(tile):
            return False
        if max(ImageStat.Stat(tile.convert('RGB')).stddev) < 10:
            return False
        # Broad exclusion, without coarse pruning: a near-lookalike or an
        # already present animation cannot arm the toast-free success route.
        return not self._matches(tile, 12, .85, prune=False)
