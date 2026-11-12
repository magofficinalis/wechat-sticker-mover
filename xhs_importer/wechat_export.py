"""Original-image-only export with bounded trusted-CDN I/O and resumable cache."""
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import hashlib
import http.client
from io import BytesIO
from pathlib import Path
import re
import sqlite3
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import warnings

from PIL import Image

from .catalog import file_digest
from .wechat_reader import check_stop
from ._vendor.wechat_meme_export.formats import detect_format, aes_decrypt_variants

MAX_IMAGE_BYTES = 64 * 1024 * 1024
CDN_SUFFIXES = ('qpic.cn', 'qlogo.cn')
CDN_HOSTS = {'wxapp.tc.qq.com', 'vweixinf.tc.qq.com', 'wxsnsdy.wxs.qq.com',
             'wxsnsdythumb.wxs.qq.com', 'snsvideo.c2c.wechat.com'}


@dataclass
class ExportResult:
    paths: list = field(default_factory=list)
    exported: int = 0
    cached: int = 0
    failed: int = 0
    issues: list = field(default_factory=list)


def validate_url(url):
    if not isinstance(url, str) or len(url) > 8192:
        raise ValueError('下载地址无效。')
    try:
        parsed = urllib.parse.urlsplit(url)
        host = (parsed.hostname or '').lower()
        allowed = host in CDN_HOSTS or any(host == suffix or host.endswith('.' + suffix) for suffix in CDN_SUFFIXES)
        if parsed.scheme not in {'https', 'http'} or not allowed or parsed.username or parsed.password:
            raise ValueError
        if parsed.port not in (None, 80 if parsed.scheme == 'http' else 443):
            raise ValueError
    except ValueError:
        raise ValueError('原图地址不属于本版本支持的微信 CDN。') from None
    return urllib.parse.urldefrag(url)[0]


class TrustedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, validate_url(newurl))


def download_original(url, stop):
    url = validate_url(url)
    check_stop(stop)
    opener = urllib.request.build_opener(TrustedRedirect())
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'image/*'})
    deadline = time.monotonic() + 30
    try:
        with opener.open(request, timeout=8) as response:
            validate_url(response.geturl())
            length = response.headers.get('Content-Length')
            if length and int(length) > MAX_IMAGE_BYTES:
                raise ValueError('原图超过 64 MB。')
            data = bytearray()
            while True:
                check_stop(stop)
                if time.monotonic() > deadline:
                    raise TimeoutError
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > MAX_IMAGE_BYTES:
                    raise ValueError('原图超过 64 MB。')
            check_stop(stop)
            return bytes(data)
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError):
        # Do not expose URLs/tokens in logs or reports.
        raise ValueError('原图下载失败或超时。') from None


def validate_image(data, stop):
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError('原图超过 64 MB。')
    fmt = detect_format(data)
    if not fmt:
        raise ValueError('未取得可识别的原图。')
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(BytesIO(data)) as im:
            frames = getattr(im, 'n_frames', 1)
            if im.width * im.height > 24_000_000 or frames > 2000 or frames * im.width * im.height > 200_000_000:
                raise ValueError('原图超过本版本的图片校验上限。')
            for index in range(frames):
                check_stop(stop)
                im.seek(index)
                im.load()
    return fmt


def _write_image(images, name, data, stop):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=images, prefix='.download-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        check_stop(stop)
        destination = images / name
        temporary.replace(destination)
        return destination
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _fetch_original(row, stop, download_fn):
    urls = list(dict.fromkeys(row.get(k, '') for k in ('cdn_url', 'extern_url', 'encrypt_url') if row.get(k)))
    for url in urls:
        check_stop(stop)
        try:
            validate_url(url)
            data = download_fn(url, stop)
            check_stop(stop)
            if len(data) > MAX_IMAGE_BYTES:
                raise ValueError('原图超过 64 MB。')
            if not detect_format(data) and row.get('aes_key'):
                decoded = aes_decrypt_variants(data, row['aes_key'])
                if decoded:
                    # Keep upstream bytes, including padding, for historical dedup.
                    data = decoded[0]
            fmt = validate_image(data, stop)
            return data, fmt, hashlib.sha256(data).hexdigest()
        except (ValueError, OSError, EOFError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            continue
    return None


def _fetch_group(rows, stop, download_fn):
    # Duplicate records can contain different usable URLs or encryption keys.
    for row in rows:
        fetched = _fetch_original(row, stop, download_fn)
        if fetched is not None:
            return fetched
    return None


def export_stickers(rows, source_id, cache_dir, stop, notify, *, download_fn=download_original):
    check_stop(stop)
    groups = {}
    entries = []
    for index, row in enumerate(rows, 1):
        check_stop(stop)
        sticker = row.get('md5', '').lower()
        if not re.fullmatch('[a-f0-9]{32}', sticker):
            entries.append((index, row))
        elif sticker in groups:
            groups[sticker].append(row)
        else:
            groups[sticker] = [row]
            entries.append((index, row))
    check_stop(stop)
    cache_dir = Path(cache_dir)
    source_key = hashlib.sha256(str(source_id).encode('utf-8')).hexdigest()
    images = cache_dir / source_key[:24] / 'images'
    images.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(cache_dir / 'cache.sqlite3')
    con.execute('CREATE TABLE IF NOT EXISTS images(source TEXT,sticker TEXT,digest TEXT,filename TEXT,PRIMARY KEY(source,sticker))')
    result = ExportResult()
    ordered = {}
    pending = {}
    iterator = iter(entries)
    exhausted = False
    started = time.monotonic()
    def progress():
        finished = result.exported + result.cached + result.failed
        notify(f'准备微信原图 {finished}/{len(entries)} · 新下载 {result.exported} · 缓存复用 {result.cached} · 失败 {result.failed} · 最多 4 张并行 · {int(time.monotonic()-started)} 秒')
    try:
        # At most four downloaded images in flight; only this thread writes SQLite.
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix='wechat-image') as pool:
            while pending or not exhausted:
                check_stop(stop)
                while len(pending) < 4 and not exhausted:
                    check_stop(stop)
                    entry = next(iterator, None)
                    if entry is None:
                        exhausted = True
                        break
                    index, row = entry
                    sticker = row.get('md5', '').lower()
                    if not re.fullmatch('[a-f0-9]{32}', sticker):
                        result.failed += 1
                        result.issues.append({'item': index, 'reason': '表情记录标识无效。'})
                        continue
                    cached = con.execute('SELECT digest,filename FROM images WHERE source=? AND sticker=?', (source_key, sticker)).fetchone()
                    if cached and re.fullmatch(r'[a-f0-9]{32}_[a-f0-9]{16}\.(png|jpg|gif|webp)', cached[1]):
                        target = images / cached[1]
                        try:
                            if target.is_file() and target.stat().st_size <= MAX_IMAGE_BYTES and file_digest(target) == cached[0]:
                                ordered[index] = target
                                result.cached += 1
                                if result.cached % 20 == 0:
                                    progress()
                                continue
                        except OSError:
                            pass
                    pending[pool.submit(_fetch_group, groups[sticker], stop, download_fn)] = (index, sticker)
                progress()
                if not pending:
                    continue
                completed, _ = wait(pending, timeout=.2, return_when=FIRST_COMPLETED)
                for future in completed:
                    check_stop(stop)
                    index, sticker = pending.pop(future)
                    fetched = future.result()
                    if fetched is not None:
                        data, fmt, digest = fetched
                        name = f'{sticker}_{digest[:16]}.{fmt}'
                        target = _write_image(images, name, data, stop)
                        with con:
                            con.execute('INSERT OR REPLACE INTO images VALUES (?,?,?,?)', (source_key, sticker, digest, name))
                        ordered[index] = target
                        result.exported += 1
                    else:
                        result.failed += 1
                        result.issues.append({'item': index, 'sticker': sticker,
                            'reason': '原图下载或完整性检查未通过；未使用缩略图替代。'})
        result.paths = [ordered[index] for index in sorted(ordered)]
        result.issues.sort(key=lambda issue: issue['item'])
        progress()
        return result
    finally:
        con.close()
