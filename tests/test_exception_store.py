import sqlite3

import pytest
from PIL import Image

from xhs_importer.catalog import scan_folder
from xhs_importer.store import Store


def setup_store(tmp_path):
    for number, color in enumerate(('red', 'green', 'blue', 'yellow')):
        Image.new('RGB', (20,20), color).save(tmp_path / f'{number}.png')
    store = Store(tmp_path / 'queue.sqlite3')
    store.add('main', scan_folder(tmp_path))
    rows = store.rows('main')
    store.begin_attempt(rows[0]['id'], 'context')
    store.set_status(rows[0]['id'], 'failed', 'before submission')
    store.begin_attempt(rows[1]['id'], 'context')
    store.set_status(rows[1]['id'], 'submitting')
    store.set_status(rows[1]['id'], 'uncertain', 'possible submission')
    store.skip(rows[1]['id'], 'main')
    return store, [row['id'] for row in rows]


def test_exception_queue_persists_and_excludes_ordinary_pending(tmp_path):
    store, ids = setup_store(tmp_path)
    assert [r['id'] for r in store.exception_rows('main')] == ids[:2]
    store.resolve_many(ids[:2], 'main', False)
    assert all(r['status'] == 'pending' for r in store.rows('main'))
    assert [r['id'] for r in store.exception_rows('main')] == ids[:2]
    assert store.rows('main')[1]['attempt_submitted'] == 1
    store.close()
    store = Store(tmp_path / 'queue.sqlite3')
    assert [r['id'] for r in store.exception_rows('main')] == ids[:2]
    store.close()


@pytest.mark.parametrize('invalid', ['other_account', 'success', 'pending', 'missing', 'duplicate'])
def test_batch_confirmation_rejects_whole_selection_without_partial_changes(tmp_path, invalid):
    store, ids = setup_store(tmp_path)
    target = ids[1]
    if invalid == 'other_account':
        store.add('other', scan_folder(tmp_path))
        target = store.rows('other')[0]['id']
        store.set_status(target, 'failed')
    elif invalid == 'success':
        store.set_status(target, 'success')
    elif invalid == 'pending':
        target = ids[2]
    elif invalid == 'missing':
        target = 9999
    else:
        target = ids[0]
    before = store.rows('main')
    events = store.db.execute('SELECT * FROM events').fetchall()
    with pytest.raises(ValueError):
        store.resolve_many([ids[0], target], 'main', False)
    assert store.rows('main') == before
    assert store.db.execute('SELECT * FROM events').fetchall() == events
    store.close()


def test_bulk_confirmation_keeps_proof_and_history_without_inventing_qualification(tmp_path):
    store, ids = setup_store(tmp_path)
    store.resolve_many(ids[:2], 'main', True)
    assert not store.exception_rows('main')
    assert store.verified_formats('main', 'context', require_visual=True) == set()
    assert store.rows('main')[1]['attempt_submitted'] == 1
    notes = [r['note'] for r in store.db.execute('SELECT note FROM events WHERE item_id=?', (ids[0],))]
    assert 'before submission' in notes
    assert len(notes) == 3
    store.close()


def test_single_requeue_joins_queue_and_parking_preserves_original_files(tmp_path):
    store, ids = setup_store(tmp_path)
    store.resolve(ids[0], False)
    assert store.rows('main')[0]['recovery_queued'] == 1
    store.park_many([ids[0], ids[1]], 'main')
    rows = store.exception_rows('main')
    assert [r['status'] for r in rows] == ['skipped', 'skipped']
    assert all(r['recovery_queued'] == 0 for r in rows)
    assert (tmp_path / '0.png').is_file()
    with pytest.raises(ValueError):
        store.park_many([ids[0], ids[2]], 'main')
    assert store.rows('main')[2]['status'] == 'pending'
    store.close()


def test_recovery_column_is_added_to_legacy_database_without_changing_status(tmp_path):
    path = tmp_path / 'legacy.sqlite3'
    db = sqlite3.connect(path)
    db.execute('''CREATE TABLE items (id INTEGER PRIMARY KEY, account TEXT, digest TEXT,
        path TEXT, format TEXT, width INTEGER,height INTEGER,frames INTEGER,size INTEGER,
        status TEXT,note TEXT,updated TEXT, UNIQUE(account,digest))''')
    db.execute("INSERT INTO items (id,account,digest,path,format,status,note,updated) VALUES (1,'main','a','old.gif','GIF','skipped','old reason','old time')")
    db.commit()
    db.close()
    store = Store(path)
    row = store.exception_rows('main')[0]
    assert row['status'] == 'skipped'
    assert row['note'] == 'old reason'
    assert row['recovery_queued'] == 0
    store.close()
