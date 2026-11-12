import pytest
from test_engine import scenario, Phone


def test_skip_preserves_uncertainty_and_can_be_explicitly_reconciled(tmp_path):
    store,engine,phone,events,stop=scenario(tmp_path)
    engine.run()
    row=store.rows('main')[0]
    store.skip(row['id'],'main')
    skipped=store.rows('main')[0]
    assert skipped['status']=='skipped'
    assert skipped['attempt_submitted']==1
    assert '未确认' in skipped['note']
    assert store.verified_formats('main',row['attempt_context'])==set()
    store.resolve(row['id'],False)
    assert store.rows('main')[0]['status']=='pending'


def test_skip_rejects_active_success_or_other_account(tmp_path):
    store,engine,phone,events,stop=scenario(tmp_path)
    row=store.rows('main')[0]
    with pytest.raises(ValueError):store.skip(row['id'],'other')
    for status in ('running','submitting','success'):
        store.set_status(row['id'],status)
        with pytest.raises(ValueError):store.skip(row['id'],'main')


def recovering(phone,stop):
    def recover(profile,event):
        phone.screen='favorites'
    phone.recover_favorites=recover


def test_auto_skips_submitted_error_and_processes_next_without_retry(tmp_path):
    phone=Phone(crash=True);store,engine,phone,events,stop=scenario(tmp_path,phone)
    original=phone.click
    def click(selector):
        try:original(selector)
        finally:
            if selector['text']=='confirm':phone.crash=False
    phone.click=click;recovering(phone,stop)
    engine.run(limit=2,auto_verify=True,skip_errors=True)
    assert [r['status'] for r in store.rows('main')]==['skipped','success']
    assert phone.pressed.count('confirm')==2


def test_failed_recovery_retains_uncertain_and_does_not_advance(tmp_path):
    store,engine,phone,events,stop=scenario(tmp_path,Phone(crash=True))
    def recover(*args):raise RuntimeError('unknown page')
    phone.recover_favorites=recover
    engine.run(limit=2,auto_verify=True,skip_errors=True)
    assert [r['status'] for r in store.rows('main')]==['uncertain','pending']


def test_pause_never_activates_skip_or_next_item(tmp_path):
    store,engine,phone,events,stop=scenario(tmp_path)
    original=phone.click
    def click(selector):
        original(selector)
        if selector['text']=='confirm':stop.set()
    phone.click=click
    phone.recover_favorites=lambda *args:pytest.fail('must not recover after pause')
    engine.run(limit=2,auto_verify=True,skip_errors=True)
    assert [r['status'] for r in store.rows('main')]==['uncertain','pending']


def test_pause_during_post_recovery_device_read_retains_uncertainty(tmp_path):
    store,engine,phone,events,stop=scenario(tmp_path,Phone(crash=True))
    recovering(phone,stop)
    original=phone.identity;calls=[]
    def identity(package):
        calls.append(package)
        if len(calls)>1:stop.set()
        return original(package)
    phone.identity=identity
    engine.run(limit=2,auto_verify=True,skip_errors=True)
    assert [r['status'] for r in store.rows('main')]==['uncertain','pending']
