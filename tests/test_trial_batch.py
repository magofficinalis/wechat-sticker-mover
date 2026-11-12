import threading

import pytest
from PIL import Image

from xhs_importer.catalog import inspect_file
from xhs_importer.engine import ImportEngine
from xhs_importer.store import Store
from xhs_importer.trial_batch import FORMATS, TrialBatch, has_trial_proof
from xhs_importer.xhs_adapter import make_profile
from test_visual_engine import VisualPhone


def scenario(tmp_path):
    store=Store(tmp_path/'queue.sqlite3')
    profile=make_profile('9.33.4')
    context=profile.verification_context('main','test')
    batch=TrialBatch.create(tmp_path,store,'main',context,FORMATS)
    phone=VisualPhone()
    events=[]
    engine=ImportEngine(store,phone,profile,'main',lambda *a:events.append(a),threading.Event())
    engine.visual_timeout=.01
    return store,profile,batch,phone,engine,events


def test_sample_formats_animations_and_sessions_are_distinct(tmp_path):
    store,_,batch,_,_,_=scenario(tmp_path)
    rows=batch.rows(store)
    assert [r['format'] for r in rows]==list(FORMATS)
    assert all(r['status']=='pending' for r in rows)
    for row in rows:
        item=inspect_file(row['path'])
        assert not item.error and item.format==row['format']
        assert (item.frames>1)==(item.format in {'GIF','WEBP'})
        with Image.open(row['path']) as im:
            first=im.convert('RGB').tobytes()
            if item.frames>1:
                im.seek(1)
                assert im.convert('RGB').tobytes()!=first
    loaded=TrialBatch.load(tmp_path)
    assert loaded==batch
    other=TrialBatch.create(tmp_path,store,'main','new-context',FORMATS)
    assert set(other.item_ids).isdisjoint(batch.item_ids)
    assert {r['digest'] for r in other.rows(store)}.isdisjoint(r['digest'] for r in rows)
    store.close()


def test_four_trials_wait_for_one_user_confirmation_and_never_touch_other_items(tmp_path):
    store,profile,batch,phone,engine,events=scenario(tmp_path)
    path=tmp_path/'ordinary.png'
    Image.new('RGB',(20,20),'red').save(path)
    store.add('main',[inspect_file(path)])
    engine.run(None,trial_batch_ids=batch.item_ids)
    rows=batch.rows(store)
    assert len(rows)==4 and all(r['status']=='uncertain' and has_trial_proof(r,batch.context) for r in rows)
    assert not profile.tested
    assert store.rows('main')[-1]['status']=='pending'
    assert sum(s.get('text')=='添加' for s in phone.clicks)==4
    store.confirm_trials(batch.item_ids,'main',batch.context)
    assert all(r['status']=='success' for r in batch.rows(store))
    assert store.verified_formats('main',batch.context,True)==set(FORMATS)
    store.close()


@pytest.mark.parametrize('problem',['missing_proof','wrong_context','wrong_account','partial'])
def test_confirmation_rejects_entire_batch_without_partial_updates(tmp_path,problem):
    store,_,batch,_,engine,_=scenario(tmp_path)
    engine.run(None,trial_batch_ids=batch.item_ids)
    if problem=='missing_proof':
        store.db.execute('UPDATE items SET attempt_visual=0 WHERE id=?',(batch.item_ids[-1],))
    elif problem=='partial':
        store.db.execute("UPDATE items SET status='pending' WHERE id=?",(batch.item_ids[-1],))
    store.db.commit()
    before=store.rows('main')
    with pytest.raises(ValueError):
        store.confirm_trials(batch.item_ids,'other' if problem=='wrong_account' else 'main',
                             'different' if problem=='wrong_context' else batch.context)
    assert store.rows('main')==before
    store.close()


def test_pause_and_resume_preserve_proven_trials_without_resubmitting(tmp_path):
    store,_,batch,phone,engine,_=scenario(tmp_path)
    original=engine.notify
    def pause(kind,message):
        original(kind,message)
        if kind=='trial-progress':
            engine.stop.set()
    engine.notify=pause
    engine.run(None,trial_batch_ids=batch.item_ids)
    assert [r['status'] for r in batch.rows(store)]==['uncertain','pending','pending','pending']
    engine.stop.clear()
    engine.notify=original
    engine.run(None,trial_batch_ids=batch.item_ids)
    assert all(r['status']=='uncertain' for r in batch.rows(store))
    assert sum(s.get('text')=='添加' for s in phone.clicks)==4
    store.close()


def test_missing_proof_stops_trial_batch_and_does_not_borrow_proven_sibling(tmp_path):
    store,_,batch,phone,engine,_=scenario(tmp_path)
    phone.verify_visual=lambda evidence:sum(s.get('text')=='添加' for s in phone.clicks)==1
    engine.run(None,trial_batch_ids=batch.item_ids)
    assert [r['status'] for r in batch.rows(store)]==['uncertain','uncertain','pending','pending']
    assert [r['attempt_visual'] for r in batch.rows(store)]==[1,0,0,0]
    clicks=list(phone.clicks)
    with pytest.raises(ValueError):
        engine.run(None,trial_batch_ids=batch.item_ids)
    assert phone.clicks==clicks
    store.close()


@pytest.mark.parametrize('problem',['auto','oversized','same_format','unrelated_uncertain','other_account','changed_context','custom'])
def test_trial_batch_boundaries_fail_before_device_clicks(tmp_path,problem):
    store,_,batch,phone,engine,_=scenario(tmp_path)
    ids=batch.item_ids
    if problem in {'oversized','same_format','unrelated_uncertain','other_account'}:
        extra=TrialBatch.create(tmp_path,store,'other' if problem=='other_account' else 'main',batch.context,['PNG'])
        if problem=='oversized': ids=ids+extra.item_ids
        elif problem=='same_format': ids=[batch.items['PNG'],extra.items['PNG']]
        elif problem=='other_account': ids=extra.item_ids
        else: store.set_status(extra.item_ids[0],'uncertain')
    elif problem=='changed_context':
        store.begin_attempt(ids[0],'previous-context')
        store.set_status(ids[0],'submitting')
        store.record_visual(ids[0])
        store.set_status(ids[0],'uncertain')
    elif problem=='custom': engine.profile.verifier=''
    with pytest.raises(ValueError):
        engine.run(None,auto_verify=problem=='auto',trial_batch_ids=ids)
    assert not phone.clicks
    store.close()


@pytest.mark.parametrize('fmt',FORMATS)
def test_replacement_card_is_visually_distinct_from_previous_session(tmp_path,fmt):
    from xhs_importer.trial_batch import make_sample
    from xhs_importer.visual_matcher import SourceMatcher
    old=make_sample(tmp_path/'old',fmt,'0123456789abcdef0123456789abcdef')
    new=make_sample(tmp_path/'new',fmt,'fedcba9876543210fedcba9876543210')
    old_matcher,new_matcher=SourceMatcher(old),SourceMatcher(new)
    for index in range(len(old_matcher.frames)):
        tile=Image.new('RGBA',(136,136),'white')
        content=old_matcher._content(index,120/136,(136,136))
        tile.alpha_composite(content,((136-content.width)//2,(136-content.height)//2))
        tile=tile.convert('RGB')
        assert old_matcher.matches(tile)
        assert not new_matcher.matches(tile)
        assert new_matcher.distinct_from(tile)


def test_setup_samples_never_enter_ordinary_import_queue(tmp_path):
    store,_,batch,phone,engine,_=scenario(tmp_path)
    engine.run(1)
    assert not phone.clicks
    assert all(r['status']=='pending' for r in batch.rows(store))
    with pytest.raises(ValueError):
        engine.run(None,item_ids=batch.item_ids)
    store.close()
