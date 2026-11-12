from pathlib import Path
from PIL import Image
from xhs_importer.catalog import scan_folder
from xhs_importer.store import Store


def pictures(tmp_path):
    folder = tmp_path / '我的表情'
    folder.mkdir()
    Image.new('RGB', (20, 30), 'red').save(folder / '静态.png')
    (folder / '重复.png').write_bytes((folder / '静态.png').read_bytes())
    Image.new('RGB', (12, 12), 'red').save(
        folder / '动图.gif', save_all=True,
        append_images=[Image.new('RGB', (12, 12), 'blue')], duration=100, loop=0)
    (folder / '损坏.jpg').write_bytes(b'not an image')
    (folder / '说明.txt').write_text('ignored')
    return folder


def test_scan_keeps_frames_and_original_bytes(tmp_path):
    folder = pictures(tmp_path)
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    rows = scan_folder(folder)
    assert len(rows) == 4
    gif = next(i for i in rows if i.path.suffix == '.gif')
    assert (gif.frames, gif.width, gif.height, gif.format) == (2, 12, 12, 'GIF')
    assert next(i for i in rows if i.path.suffix == '.jpg').error
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == before


def test_queue_deduplicates_content_per_account(tmp_path):
    items = scan_folder(pictures(tmp_path))
    store = Store(tmp_path / 'queue.db')
    assert store.add('main', items) == (2, 1, 1)
    assert len(store.rows('main')) == 2
    assert store.add('main', items) == (0, 3, 1)
    assert store.add('second', items) == (2, 1, 1)
    store.close()


def test_crash_recovery_never_requeues_possibly_submitted_image(tmp_path):
    items = [i for i in scan_folder(pictures(tmp_path)) if not i.error]
    store = Store(tmp_path / 'queue.db')
    store.add('main', items)
    rows = store.rows('main')
    store.set_status(rows[0]['id'], 'submitting')
    store.set_status(rows[1]['id'], 'running')
    store.close()
    store = Store(tmp_path / 'queue.db')
    assert store.recover('main') == 2
    assert {r['status'] for r in store.rows('main')} == {'uncertain'}
    store.resolve(rows[0]['id'], imported=True)
    store.resolve(rows[1]['id'], imported=False)
    assert [r['status'] for r in store.rows('main')] == ['success', 'pending']
    store.close()


def test_success_cannot_be_reset_by_resolve(tmp_path):
    store = Store(tmp_path / 'queue.db')
    store.add('main', scan_folder(pictures(tmp_path)))
    row = store.rows('main')[0]
    store.set_status(row['id'], 'success')
    import pytest
    with pytest.raises(ValueError):
        store.resolve(row['id'], imported=False)
    assert store.rows('main')[0]['status'] == 'success'
    store.close()


def test_duplicate_after_restart_refreshes_path_and_preserves_history(tmp_path):
    import pytest
    folder = tmp_path / 'source'
    folder.mkdir()
    path = folder / 'old.png'
    Image.new('RGB', (20, 30), 'red').save(path)
    db = tmp_path / 'queue.db'
    for state in ('success', 'skipped', 'uncertain', 'failed', 'pending', 'running', 'submitting'):
        store = Store(db)
        store.add(state, scan_folder(folder))
        row = store.rows(state)[0]
        store.set_status(row['id'], state, 'original note')
        store.close()
    renamed = folder / 'new.png'
    path.rename(renamed)
    store = Store(db)
    for state in ('success', 'skipped', 'uncertain', 'failed', 'pending', 'running', 'submitting'):
        details = []
        assert store.add(state, scan_folder(folder), duplicates=details) == (0, 1, 0)
        row = store.rows(state)[0]
        assert row['status'] == state
        assert row['note'] == 'original note'
        assert row['path'] == str(path if state in {'running', 'submitting'} else renamed)
        assert details[0]['status'] == state
        assert details[0]['path'] == str(renamed)
        assert details[0]['id'] == row['id']
    store.close()
