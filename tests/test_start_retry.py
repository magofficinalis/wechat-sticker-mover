import pytest
from test_engine import scenario


def immediate_wait(engine):
    original = engine.wait_selector
    engine.wait_selector = lambda selector, package, timeout=10: original(selector, package, timeout=0)


def failed_before_submit(store, row_id):
    store.begin_attempt(row_id, 'old-verified-context', 'auto')
    store.set_status(row_id, 'failed', '等待收藏页面超时')


def test_wrong_start_page_keeps_pending_without_attempt_or_cleanup(tmp_path):
    store, engine, phone, events, stop = scenario(tmp_path)
    immediate_wait(engine)
    phone.screen = 'picker'
    cleanup = []
    phone.cleanup_staging = lambda *args, **kw: cleanup.append(True)
    engine.run(1, auto_verify=True, skip_errors=True)
    assert [r['status'] for r in store.rows('main')] == ['pending', 'pending']
    assert store.db.execute('SELECT count(*) FROM events').fetchone()[0] == 0
    assert not phone.pressed and not cleanup
    assert any(kind == 'blocked' and '尚未开始' in text for kind, text in events)
    phone.screen = 'favorites'
    engine.run(None, auto_verify=True, item_ids=[store.rows('main')[0]['id']])
    assert store.rows('main')[0]['status'] == 'success'
    store.close()


def test_selected_unsubmitted_failure_retries_after_page_is_ready(tmp_path):
    store, engine, phone, events, stop = scenario(tmp_path)
    immediate_wait(engine)
    row_id = store.rows('main')[0]['id']
    failed_before_submit(store, row_id)
    phone.screen = 'picker'
    engine.run(None, auto_verify=True, item_ids=[row_id])
    assert store.rows('main')[0]['status'] == 'failed'
    phone.screen = 'favorites'
    engine.run(None, auto_verify=True, item_ids=[row_id])
    assert [r['status'] for r in store.rows('main')] == ['success', 'pending']
    assert phone.pressed.count('confirm') == 1
    store.close()


def test_single_trial_can_retry_known_unsubmitted_failure(tmp_path):
    store, engine, phone, events, stop = scenario(tmp_path)
    row_id = store.rows('main')[0]['id']
    failed_before_submit(store, row_id)
    engine.run(1, item_id=row_id)
    assert store.rows('main')[0]['status'] == 'uncertain'
    assert phone.pressed.count('confirm') == 1
    store.close()


def test_page_lost_between_items_leaves_next_pending(tmp_path):
    store, engine, phone, events, stop = scenario(tmp_path)
    immediate_wait(engine)
    def cleanup(*args, **kwargs):
        if kwargs.get('item_id') is not None:
            phone.screen = 'picker'
    phone.cleanup_staging = cleanup
    engine.run(None, auto_verify=True, skip_errors=True)
    assert [r['status'] for r in store.rows('main')] == ['success', 'pending']
    assert phone.pressed.count('confirm') == 1
    assert any(kind == 'blocked' for kind, _ in events)
    store.close()


@pytest.mark.parametrize('state,submitted,proof', [
    ('failed', True, True), ('failed', False, False), ('success', False, True), ('skipped', False, True)])
def test_selected_unsafe_states_never_directly_retry(tmp_path, state, submitted, proof):
    store, engine, phone, events, stop = scenario(tmp_path)
    row_id = store.rows('main')[0]['id']
    if proof:
        store.begin_attempt(row_id, 'old-context', 'auto')
    if submitted:
        store.set_status(row_id, 'submitting')
    store.set_status(row_id, state)
    with pytest.raises(ValueError):
        engine.run(None, auto_verify=True, item_ids=[row_id, store.rows('main')[1]['id']])
    assert not phone.pressed
    assert store.rows('main')[0]['status'] == state
    assert store.rows('main')[1]['status'] == 'pending'
    store.close()


def test_guarded_requeue_rejects_wrong_account_or_possible_submission(tmp_path):
    store, engine, phone, events, stop = scenario(tmp_path)
    row_id = store.rows('main')[0]['id']
    failed_before_submit(store, row_id)
    with pytest.raises(ValueError):
        store.retry_unsubmitted(row_id, 'other')
    store.set_status(row_id, 'submitting')
    store.set_status(row_id, 'failed')
    with pytest.raises(ValueError):
        store.retry_unsubmitted(row_id, 'main')
    assert store.rows('main')[0]['status'] == 'failed'
    store.close()
