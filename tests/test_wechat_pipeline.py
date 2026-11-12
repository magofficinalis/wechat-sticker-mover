from pathlib import Path
import threading

from PIL import Image

from xhs_importer.store import Store
from xhs_importer.catalog import scan_folder
from xhs_importer.wechat_pipeline import queue_export, run_wechat_pipeline, preflight_import
from xhs_importer.wechat_export import ExportResult


def test_batch_includes_matching_pending_but_not_unrelated_or_historical_states(tmp_path):
    folder = tmp_path / 'images'
    folder.mkdir()
    for i, color in enumerate(['red', 'blue', 'green', 'yellow', 'pink']):
        Image.new('RGB', (20, 20), color).save(folder / f'{i}.png')
    Image.new('RGB', (20, 20), 'black').save(folder / 'new.webp')
    store = Store(tmp_path / 'queue.sqlite3')
    store.add('main', scan_folder(folder))
    rows = store.rows('main')
    store.set_status(rows[1]['id'], 'success')
    store.set_status(rows[2]['id'], 'skipped')
    store.set_status(rows[3]['id'], 'uncertain')
    paths = [Path(r['path']) for r in rows if Path(r['path']).name != '4.png']
    batch = queue_export(store, 'main', paths, ['PNG'], threading.Event())
    assert batch.eligible_ids == [rows[0]['id']]
    assert batch.unverified_ids == [rows[5]['id']]
    assert [r['status'] for r in store.rows('main')] == ['pending','success','skipped','uncertain','pending','pending']
    other = queue_export(store, 'other', paths, ['PNG'], threading.Event())
    assert len(other.eligible_ids) == 4
    store.close()


def test_cancel_between_export_and_import_never_submits(tmp_path):
    stop = threading.Event()
    calls = []
    def export(*args, **kwargs):
        stop.set()
        return ExportResult()
    import pytest
    with pytest.raises(Exception, match='暂停'):
        run_wechat_pipeline(tmp_path / 'emoticon.db', tmp_path, 'main', ['PNG'], stop, lambda _: None,
            read_fn=lambda *args: [], export_fn=export, import_fn=lambda ids: calls.append(ids))
    assert not calls


def test_preflight_rejects_wrong_screen_before_export(tmp_path):
    from test_engine import scenario
    from xhs_importer.config import Settings
    import pytest
    store, engine, phone, events, stop = scenario(tmp_path)
    phone.current_package = lambda: 'other.app'
    settings = Settings('adb.exe', 'emulator-1', 'main', '')
    engine.profile.tested_context = engine.profile.verification_context('main', 'emulator-1', 'adb.exe')
    with pytest.raises(RuntimeError, match='前台'):
        preflight_import(settings, engine.profile, store, phone, stop)
    assert phone.pressed == []
    store.close()


def test_pause_before_submission_stays_pending_for_resume(tmp_path):
    from test_engine import scenario
    from xhs_importer.engine import Cancelled
    store, engine, phone, events, stop = scenario(tmp_path)
    def cancelled_stage(row):
        stop.set()
        raise Cancelled('已暂停')
    phone.stage = cancelled_stage
    engine.run(1, auto_verify=False)
    assert store.rows('main')[0]['status'] == 'pending'
    assert '已暂停' in store.rows('main')[0]['note']
    assert phone.pressed == []
    store.close()


def test_known_unchanged_image_reuses_metadata_but_changed_bytes_are_checked(tmp_path, monkeypatch):
    import xhs_importer.wechat_pipeline as pipeline
    path = tmp_path / 'one.gif'
    Image.new('RGB', (20, 20), 'red').save(path)
    store = Store(tmp_path / 'queue.db')
    store.add('main', scan_folder(tmp_path))
    calls = []
    original = pipeline.inspect_file
    def inspect(path, check=None):
        calls.append(path)
        return original(path, check)
    monkeypatch.setattr(pipeline, 'inspect_file', inspect)
    try:
        result = queue_export(store, 'main', [path], ['GIF'], threading.Event())
        assert not calls and result.duplicate == 1
        path.write_bytes(b'broken')
        result = queue_export(store, 'main', [path], ['GIF'], threading.Event())
        assert calls == [path] and result.invalid == 1 and not result.source_ids
    finally:
        store.close()
