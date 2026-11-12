from io import BytesIO
import threading

import pytest
from PIL import Image

from xhs_importer.wechat_export import export_stickers, validate_url, TrustedRedirect


def picture(fmt='PNG', animated=False):
    stream = BytesIO()
    kwargs = {'save_all': True, 'append_images': [Image.new('RGB', (20, 20), 'blue')], 'duration': 120, 'loop': 0} if animated else {}
    Image.new('RGB', (20, 20), 'red').save(stream, format=fmt, **kwargs)
    return stream.getvalue()


def row(md5='a' * 32):
    return {'md5': md5, 'aes_key': '', 'cdn_url': 'https://emoji.qpic.cn/original',
            'extern_url': '', 'encrypt_url': '', 'thumb_url': 'https://emoji.qpic.cn/thumbnail'}


def test_incremental_reuses_cache_and_repairs_missing_or_changed_file(tmp_path):
    calls = []
    def download(url, stop):
        calls.append(url)
        return picture('GIF', True)
    stop = threading.Event()
    first = export_stickers([row()], 'source', tmp_path, stop, lambda _: None, download_fn=download)
    assert first.exported == 1 and first.cached == 0 and first.failed == 0
    with Image.open(first.paths[0]) as im:
        assert im.n_frames == 2
    again = export_stickers([row()], 'source', tmp_path, stop, lambda _: None, download_fn=download)
    assert again.cached == 1 and len(calls) == 1
    first.paths[0].write_bytes(b'corrupt')
    repaired = export_stickers([row()], 'source', tmp_path, stop, lambda _: None, download_fn=download)
    assert repaired.exported == 1 and len(calls) == 2
    repaired.paths[0].unlink()
    export_stickers([row()], 'source', tmp_path, stop, lambda _: None, download_fn=download)
    assert len(calls) == 3


def test_original_failure_never_downloads_thumbnail_and_one_bad_image_does_not_stop_next(tmp_path):
    calls = []
    bad, good = row(), row('b' * 32)
    good['cdn_url'] = 'https://emoji.qpic.cn/good'
    def download(url, stop):
        calls.append(url)
        return picture() if url.endswith('/good') else b'broken'
    result = export_stickers([bad, good], 'source', tmp_path, threading.Event(), lambda _: None, download_fn=download)
    assert result.failed == 1 and result.exported == 1
    assert len(calls) == 2 and not any('thumbnail' in u for u in calls)
    assert 'https://' not in str(result.issues)


@pytest.mark.parametrize('url', ['file:///c:/secret', 'https://evil.test/x', 'http://127.0.0.1/x',
    'https://emoji.qpic.cn.evil.test/x', 'https://user:pass@emoji.qpic.cn/x', 'https://emoji.qpic.cn:8080/x'])
def test_only_supported_cdn_urls_allowed(url):
    with pytest.raises(ValueError):
        validate_url(url)


def test_redirect_revalidates_destination():
    import urllib.request
    handler = TrustedRedirect()
    request = urllib.request.Request('https://emoji.qpic.cn/start')
    with pytest.raises(ValueError):
        handler.redirect_request(request, None, 302, 'Found', {}, 'http://localhost/private')


def test_observed_wechat_cdn_hosts_are_supported():
    for host in ('wxapp.tc.qq.com', 'vweixinf.tc.qq.com', 'mmbiz.qpic.cn', 'snsvideo.c2c.wechat.com'):
        assert validate_url(f'https://{host}/original') == f'https://{host}/original'


def test_invalid_record_and_cancellation_never_write_partial_success(tmp_path):
    stop = threading.Event()
    calls = []
    def download(url, event):
        calls.append(url)
        event.set()
        return picture()
    result = export_stickers([row('../bad')], 'source', tmp_path, stop, lambda _: None, download_fn=download)
    assert result.failed == 1 and not calls
    with pytest.raises(Exception, match='暂停'):
        export_stickers([row()], 'source', tmp_path, stop, lambda _: None, download_fn=download)
    assert not list(tmp_path.rglob('*.png'))


def test_source_caches_are_separate(tmp_path):
    def download(url, stop):
        return picture()
    stop = threading.Event()
    first = export_stickers([row()], 'one', tmp_path, stop, lambda _: None, download_fn=download)
    second = export_stickers([row()], 'two', tmp_path, stop, lambda _: None, download_fn=download)
    assert first.paths != second.paths


def test_truncated_http_response_records_one_failure_and_continues(tmp_path, monkeypatch):
    import http.client
    from types import SimpleNamespace
    class Response(BytesIO):
        headers = {}
        def __init__(self, url):
            super().__init__(picture())
            self.url = url
        def geturl(self):
            return self.url
        def read(self, size=-1):
            if self.url.endswith('/original'):
                raise http.client.IncompleteRead(b'partial', 100)
            return super().read(size)
    opener = SimpleNamespace(open=lambda request, timeout: Response(request.full_url))
    monkeypatch.setattr('urllib.request.build_opener', lambda *args: opener)
    good = row('b' * 32)
    good['cdn_url'] = 'https://emoji.qpic.cn/good'
    result = export_stickers([row(), good], 'source', tmp_path, threading.Event(), lambda _: None)
    assert result.failed == 1 and result.exported == 1


def test_encrypted_export_preserves_upstream_bytes_and_historical_success(tmp_path):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from xhs_importer._vendor.wechat_meme_export.formats import aes_decrypt_variants
    from xhs_importer.catalog import inspect_file
    from xhs_importer.store import Store
    from xhs_importer.wechat_pipeline import queue_export
    original = picture()
    pad = 16 - len(original) % 16
    padded = original + bytes([pad]) * pad
    key = bytes(range(16))
    encryptor = Cipher(algorithms.AES(key), modes.CBC(bytes(16))).encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    upstream_bytes, _ = aes_decrypt_variants(encrypted, key.hex())
    previous = tmp_path / 'previous.png'
    previous.write_bytes(upstream_bytes)
    store = Store(tmp_path / 'queue.sqlite3')
    try:
        store.add('main', [inspect_file(previous)])
        old_id = store.rows('main')[0]['id']
        store.set_status(old_id, 'success')
        sticker = row()
        sticker['aes_key'] = key.hex()
        stop = threading.Event()
        exported = export_stickers([sticker], 'source', tmp_path / 'cache', stop, lambda _: None,
            download_fn=lambda *args: encrypted)
        assert exported.paths[0].read_bytes() == upstream_bytes
        batch = queue_export(store, 'main', exported.paths, ['PNG'], stop)
        assert batch.eligible_ids == [] and batch.duplicate == 1
        assert len(store.rows('main')) == 1
        assert store.rows('main')[0]['status'] == 'success'
    finally:
        store.close()


def test_original_downloads_overlap_with_at_most_four_in_flight(tmp_path):
    import time
    lock = threading.Lock()
    active = peak = 0
    def download(url, stop):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(.04)
        with lock:
            active -= 1
        return picture()
    rows = [row(f'{i:032x}') for i in range(12)]
    result = export_stickers(rows, 'source', tmp_path, threading.Event(), lambda _: None, download_fn=download)
    assert 2 <= peak <= 4
    assert result.exported == 12 and result.failed == 0
    assert [p.name.split('_')[0] for p in result.paths] == [r['md5'] for r in rows]


def test_duplicate_wechat_record_is_downloaded_only_once(tmp_path):
    calls = []
    result = export_stickers([row(), row()], 'source', tmp_path, threading.Event(), lambda _: None,
        download_fn=lambda *args: calls.append(True) or picture())
    assert len(calls) == 1 and len(result.paths) == 1


def test_duplicate_record_can_supply_missing_original_url(tmp_path):
    first, second = row(), row()
    first['cdn_url'] = ''
    calls = []
    result = export_stickers([first, second], 'source', tmp_path, threading.Event(), lambda _: None,
        download_fn=lambda *args: calls.append(True) or picture())
    assert result.exported == 1 and result.failed == 0 and len(calls) == 1


def test_cancel_while_grouping_does_not_leave_cache_connection_open(tmp_path, monkeypatch):
    import sqlite3
    from xhs_importer.engine import Cancelled
    stop = threading.Event()
    connections = []
    connect = sqlite3.connect
    def tracked_connect(*args, **kwargs):
        con = connect(*args, **kwargs)
        connections.append(con)
        return con
    class CancelRow(dict):
        def get(self, key, default=None):
            stop.set()
            return super().get(key, default)
    monkeypatch.setattr(sqlite3, 'connect', tracked_connect)
    try:
        with pytest.raises(Cancelled):
            export_stickers([CancelRow(row()), row('b' * 32)], 'source', tmp_path, stop, lambda _: None)
        for con in connections:
            with pytest.raises(sqlite3.ProgrammingError):
                con.execute('SELECT 1')
    finally:
        for con in connections:
            con.close()
