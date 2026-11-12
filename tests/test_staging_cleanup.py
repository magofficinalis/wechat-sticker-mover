import threading
import pytest
from xhs_importer.device import Adb
from test_engine import scenario, Phone


TOKEN = '012345abcdef'
DIGEST = 'a' * 64
REMOTE = f'/sdcard/DCIM/XHSImport_{TOKEN}/{DIGEST[:20]}_{TOKEN}.png'


class MediaAdb(Adb):
    def __init__(self):
        super().__init__('adb')
        self.file = self.index = True
        self.digest = DIGEST
        self.calls = []
        self.redirect = False
        self.remote = REMOTE

    def shell(self, serial, args, timeout=25):
        self.calls.append(args)
        if args[0] == 'realpath':
            return '/private/elsewhere' if self.redirect and args[1] != '/sdcard/DCIM' else args[1].replace('/sdcard', '/storage/emulated/0')
        if args[:2] == ['sh', '-c']:
            return 'file' if self.file else 'absent'
        if args[0] == 'sha256sum':
            return self.digest + '  ' + args[-1]
        if args[:2] == ['content', 'query']:
            return f'Row: 0 _id=42, _data={self.remote.replace("/sdcard", "/storage/emulated/0")}' if self.index else 'No result found.'
        if args[:2] == ['content', 'delete']:
            self.index = False
            return 'Deleted 1 rows.'
        if args[0] == 'rm':
            self.file = False
        return ''


def test_cleanup_removes_exact_media_and_empty_album_only():
    from xhs_importer.staging_cleanup import remove_staged
    adb = MediaAdb()
    remove_staged(adb, 'device', REMOTE, DIGEST, threading.Event())
    assert not adb.file and not adb.index
    assert ['rm', '-f', REMOTE] in adb.calls
    assert ['rmdir', REMOTE.rsplit('/', 1)[0]] in adb.calls
    assert not any('-r' in arg or '-rf' in arg for args in adb.calls for arg in args)


@pytest.mark.parametrize('bad', ['/sdcard/DCIM/Camera/a.png', REMOTE.replace(TOKEN, '../outside'), REMOTE + '/../../a.png'])
def test_unowned_path_is_rejected_before_any_android_command(bad):
    from xhs_importer.staging_cleanup import remove_staged
    adb = MediaAdb()
    with pytest.raises(ValueError):
        remove_staged(adb, 'device', bad, DIGEST, threading.Event())
    assert not adb.calls


@pytest.mark.parametrize('mismatch', ['hash', 'symlink'])
def test_changed_or_redirected_file_is_never_deleted(mismatch):
    from xhs_importer.staging_cleanup import remove_staged
    adb = MediaAdb()
    if mismatch == 'hash': adb.digest = 'b' * 64
    else: adb.redirect = True
    with pytest.raises(ValueError):
        remove_staged(adb, 'device', REMOTE, DIGEST, threading.Event())
    assert adb.file and adb.index


def test_manifest_only_returns_success_for_same_account_and_device(tmp_path):
    store, engine, phone, events, stop = scenario(tmp_path)
    first, second = store.rows('main')
    def remote(row, token):
        return f'/sdcard/DCIM/XHSImport_{token}/{row["digest"][:20]}_{token}.png'
    store.record_staged(first['id'], 'device', remote(first, TOKEN))
    store.record_staged(second['id'], 'device', remote(second, '123456abcdef'))
    store.set_status(first['id'], 'success')
    store.set_status(second['id'], 'skipped')
    assert len(store.cleanup_candidates('main', 'device')) == 1
    assert not store.cleanup_candidates('other', 'device')
    assert not store.cleanup_candidates('main', 'other-device')
    candidate = store.cleanup_candidates('main', 'device')[0]
    store.finish_cleanup('device', candidate['remote'])
    assert not store.cleanup_candidates('main', 'device')
    store.resolve(second['id'], True)
    assert len(store.cleanup_candidates('main', 'device')) == 1
    store.close()


def test_engine_cleans_after_success_and_cleanup_error_cannot_downgrade(tmp_path):
    store, engine, phone, events, stop = scenario(tmp_path)
    observed = []
    def cleanup(store, account, event, notify, **kwargs):
        if kwargs.get('item_id') is not None:
            observed.append(store.rows(account)[0]['status'])
            raise RuntimeError('cleanup unavailable')
    phone.cleanup_staging = cleanup
    engine.run(1, auto_verify=True)
    assert observed == ['success']
    assert store.rows('main')[0]['status'] == 'success'
    store.close()


@pytest.mark.parametrize('mode', ['trial', 'failed', 'skipped'])
def test_non_success_keeps_staged_media(tmp_path, mode):
    store, engine, phone, events, stop = scenario(tmp_path, Phone(crash=mode != 'trial'))
    cleaned = []
    phone.cleanup_staging = lambda *args, **kw: cleaned.append(kw.get('item_id'))
    phone.recover_favorites = lambda *args: setattr(phone, 'screen', 'favorites')
    engine.run(1, auto_verify=mode != 'trial', skip_errors=mode == 'skipped')
    assert not any(item_id is not None for item_id in cleaned)
    assert store.rows('main')[0]['status'] in {'uncertain', 'skipped'}
    store.close()


def test_legacy_adoption_keeps_unknown_and_skipped_images(tmp_path):
    from xhs_importer.staging_cleanup import cleanup_successful
    store, engine, phone, events, stop = scenario(tmp_path)
    first = store.rows('main')[0]
    # Only a persisted success can qualify an old media entry.
    adb = MediaAdb()
    cleanup_successful(store, adb, 'device', 'main', stop, lambda _: None, discover=True)
    assert adb.file and adb.index
    store.set_status(first['id'], 'success')
    # The old entry still has a different digest prefix and must remain.
    cleanup_successful(store, adb, 'device', 'main', stop, lambda _: None, discover=True)
    assert adb.file and adb.index
    store.close()


def test_legacy_matching_success_cleans_and_skipped_matching_file_stays(tmp_path):
    from xhs_importer.staging_cleanup import cleanup_successful
    store, engine, phone, events, stop = scenario(tmp_path)
    first = store.rows('main')[0]
    adb = MediaAdb()
    adb.remote = f'/sdcard/DCIM/XHSImport_{TOKEN}/{first["digest"][:20]}_{TOKEN}.png'
    adb.digest = first['digest']
    store.set_status(first['id'], 'skipped')
    assert cleanup_successful(store, adb, 'device', 'main', stop, lambda _: None, discover=True) == 0
    assert adb.file and adb.index
    store.resolve(first['id'], True)
    assert cleanup_successful(store, adb, 'device', 'main', stop, lambda _: None, discover=True) == 1
    assert not adb.file and not adb.index
    assert not store.cleanup_candidates('main', 'device')
    store.close()


def test_pause_before_cleanup_never_touches_android():
    from xhs_importer.staging_cleanup import remove_staged
    stop = threading.Event()
    stop.set()
    adb = MediaAdb()
    with pytest.raises(RuntimeError, match='暂停'):
        remove_staged(adb, 'device', REMOTE, DIGEST, stop)
    assert not adb.calls


def test_missing_file_stale_media_index_is_cleaned_idempotently():
    from xhs_importer.staging_cleanup import remove_staged
    adb = MediaAdb()
    adb.file = False
    remove_staged(adb, 'device', REMOTE, DIGEST, threading.Event())
    remove_staged(adb, 'device', REMOTE, DIGEST, threading.Event())
    assert not adb.file and not adb.index
    assert not any(args[0] == 'rm' for args in adb.calls)
