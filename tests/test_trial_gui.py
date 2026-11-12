import time
import tkinter as tk

import pytest
from PIL import Image

from xhs_importer.catalog import inspect_file
from xhs_importer.config import Settings
from xhs_importer.gui import Application
from xhs_importer.profile import Profile
from xhs_importer.trial_batch import FORMATS, TrialBatch
from xhs_importer.xhs_adapter import make_profile
from test_visual_engine import VisualPhone


def app_for(tmp_path, monkeypatch, evidence=True, blank=False):
    Settings(adb='test-adb',serial='test',account='main').save(tmp_path/'settings.json')
    if not (tmp_path/'profile.json').exists():
        (Profile() if blank else make_profile('9.33.4')).save(tmp_path/'profile.json')
    root=tk.Tk();root.withdraw()
    app=Application(root,tmp_path)
    app.confirmed.set(True)
    phone=VisualPhone(evidence)
    monkeypatch.setattr(app,'make_device',lambda:phone)
    monkeypatch.setattr('xhs_importer.engine.ImportEngine.visual_timeout',.01)
    monkeypatch.setattr(app,'cleanup_confirmed',lambda item_id=None,on_done=None:on_done() if on_done else None)
    return app,phone


def idle(app):
    deadline=time.monotonic()+6
    while app.busy and time.monotonic()<deadline:
        app.root.update();time.sleep(.01)
    app.root.update()
    assert not app.busy


def test_auto_prepare_four_trials_then_confirm_and_start_local_once(tmp_path,monkeypatch):
    app,phone=app_for(tmp_path,monkeypatch,blank=True)
    try:
        launches=[]
        monkeypatch.setattr(app,'start_product_import',lambda *a,**k:launches.append(True))
        app.trial_panel.start_trials()
        idle(app)
        session=app.trial_panel.session
        assert len(session.item_ids)==4
        assert all(r['status']=='uncertain' for r in session.rows(app.store))
        assert not app.profile.tested and not launches
        assert app.trial_panel.confirm_button.instate(['!disabled'])
        app.trial_panel.confirm_and_start()
        assert app.profile.tested_formats==sorted(FORMATS)
        assert app.profile.phone_sync and app.profile.tested_context==app.profile_context()
        assert all(r['status']=='success' for r in session.rows(app.store))
        assert TrialBatch.load(tmp_path).confirmed
        assert launches==[True]
        app.trial_panel.confirm_and_start()
        assert launches==[True]
        assert sum(s.get('text')=='添加' for s in phone.clicks)==4
    finally:
        app.close()


def test_partial_batch_cannot_confirm_or_start_normal_import(tmp_path,monkeypatch):
    app,phone=app_for(tmp_path,monkeypatch)
    try:
        phone.verify_visual=lambda _:sum(s.get('text')=='添加' for s in phone.clicks)==1
        monkeypatch.setattr(app,'start_product_import',lambda *a,**k:pytest.fail('must not start'))
        app.trial_panel.start_trials();idle(app)
        session=app.trial_panel.session
        before=session.rows(app.store)
        assert [r['status'] for r in before]==['uncertain','uncertain','pending','pending']
        app.trial_panel.confirm_and_start()
        assert not app.profile.tested
        assert session.rows(app.store)==before
        clicks=list(phone.clicks)
        app.trial_panel.start_trials();idle(app)
        assert phone.clicks==clicks
        # The user explicitly confirms the second card was NOT collected.
        app.store.resolve_many([session.item_ids[1]],'main',False)
        phone.verify_visual=lambda _:True
        app.trial_panel.start_trials();idle(app)
        assert all(r['status']=='uncertain' for r in session.rows(app.store))
        assert sum(s.get('text')=='添加' for s in phone.clicks)==5
    finally:
        app.close()


@pytest.mark.parametrize('change',['account','device','source','stop'])
def test_confirmation_continuation_rechecks_environment_after_cleanup(tmp_path,monkeypatch,change):
    app,_=app_for(tmp_path,monkeypatch)
    try:
        app.trial_panel.start_trials();idle(app)
        resumes=[]
        monkeypatch.setattr(app,'cleanup_confirmed',lambda item_id=None,on_done=None:resumes.append(on_done))
        monkeypatch.setattr(app,'start_product_import',lambda *a,**k:pytest.fail('environment changed'))
        monkeypatch.setattr(app.wechat_panel,'start',lambda *a:pytest.fail('environment changed'))
        app.trial_panel.confirm_and_start()
        assert len(resumes)==1
        if change=='account': app.account.set('other')
        elif change=='device': app.serial.set('other')
        elif change=='source': app.wechat_panel.source.set('another/emoticon.db')
        else: app.stop_event.set()
        app.confirmed.set(True)
        resumes[0]()
    finally:
        app.close()


def test_old_session_cannot_be_confirmed_in_new_context(tmp_path,monkeypatch):
    app,_=app_for(tmp_path,monkeypatch)
    try:
        app.trial_panel.start_trials();idle(app)
        session=app.trial_panel.session
        before=session.rows(app.store)
        app.serial.set('different');app.confirmed.set(True)
        app.trial_panel.confirm_and_start()
        assert not app.profile.tested
        assert session.rows(app.store)==before
    finally:
        app.close()


def test_already_verified_formats_are_not_trialed_again(tmp_path,monkeypatch):
    app,_=app_for(tmp_path,monkeypatch)
    try:
        p=tmp_path/'known.png';Image.new('RGB',(20,20),'green').save(p)
        app.store.add('main',[inspect_file(p)])
        row=app.store.rows('main')[0]
        context=app.profile_context()
        app.store.begin_attempt(row['id'],context)
        app.store.set_status(row['id'],'submitting');app.store.record_visual(row['id'])
        app.store.set_status(row['id'],'uncertain');app.store.resolve(row['id'],True)
        app.profile.tested=app.profile.phone_sync=True
        app.profile.tested_formats=['PNG'];app.profile.tested_context=context
        app.profile.save(tmp_path/'profile.json')
        app.trial_panel.start_trials();idle(app)
        assert set(app.trial_panel.session.items)=={'GIF','JPEG','WEBP'}
        assert next(r for r in app.store.rows('main') if r['id']==row['id'])['status']=='success'
    finally:
        app.close()


def test_restart_restores_unconfirmed_test_cards_without_automatic_actions(tmp_path,monkeypatch):
    # Persist the same database states produced by a completed but unconfirmed run.
    from xhs_importer.store import Store
    profile=make_profile('9.33.4');profile.save(tmp_path/'profile.json')
    context=profile.verification_context('main','test','test-adb')
    store=Store(tmp_path/'queue.sqlite3')
    batch=TrialBatch.create(tmp_path,store,'main',context,FORMATS)
    for row in batch.rows(store):
        store.begin_attempt(row['id'],context)
        store.set_status(row['id'],'submitting');store.record_visual(row['id'])
        store.set_status(row['id'],'uncertain')
    store.close()
    app,phone=app_for(tmp_path,monkeypatch)
    try:
        assert app.trial_panel.session.item_ids==batch.item_ids
        assert not phone.clicks and not app.profile.tested
        app.trial_panel.start_trials();idle(app)
        assert not phone.clicks
        assert app.trial_panel.confirm_button.instate(['!disabled'])
    finally:
        app.close()


def test_replace_collected_card_without_proof_and_finish_same_batch(tmp_path,monkeypatch):
    app,phone=app_for(tmp_path,monkeypatch,evidence=False)
    try:
        app.trial_panel.start_trials();idle(app)
        batch=app.trial_panel.session
        old_id=batch.items['GIF']
        app.trial_panel.tree.selection_set('GIF')
        app.trial_panel.replace_selected()
        assert batch.items['GIF']==old_id  # ambiguous card must be resolved first
        app.store.resolve_many([old_id],'main',True)
        app.trial_panel.replace_selected()
        new_id=batch.items['GIF']
        assert old_id!=new_id
        assert TrialBatch.load(tmp_path).items['GIF']==new_id
        assert next(r for r in app.store.rows('main') if r['id']==old_id)['status']=='success'
        phone.evidence=True
        app.trial_panel.start_trials();idle(app)
        launches=[]
        monkeypatch.setattr(app,'start_product_import',lambda *a,**k:launches.append(True))
        app.trial_panel.confirm_and_start()
        assert app.profile.tested_formats==sorted(FORMATS)
        assert launches==[True]
    finally:
        app.close()


def test_generated_cards_excluded_from_select_all_and_ordinary_queue(tmp_path,monkeypatch):
    app,_=app_for(tmp_path,monkeypatch)
    try:
        batch=TrialBatch.create(tmp_path,app.store,'main',app.profile_context(),FORMATS)
        path=tmp_path/'ordinary.png';Image.new('RGB',(20,20),'red').save(path)
        app.store.add('main',[inspect_file(path)])
        ordinary=app.store.rows('main')[-1]
        app.reload_rows();app.select_all_pending()
        assert app.tree.selection()==(str(ordinary['id']),)
        app.profile.tested=app.profile.phone_sync=True
        app.profile.tested_formats=list(FORMATS);app.profile.tested_context=app.profile_context()
        app.profile.save(tmp_path/'profile.json')
        launches=[]
        monkeypatch.setattr(app,'launch_import',lambda *a,**kw:launches.append(kw))
        app.start_product_import(False)
        assert launches[0]['item_ids']==[ordinary['id']]
        assert all(r['status']=='pending' for r in batch.rows(app.store))
    finally:
        app.close()


def test_test_card_retries_route_back_to_first_use_instead_of_ordinary_engine(tmp_path,monkeypatch):
    app,_=app_for(tmp_path,monkeypatch)
    try:
        batch=TrialBatch.create(tmp_path,app.store,'main',app.profile_context(),FORMATS)
        app.trial_panel.session=batch
        app.store.set_status(batch.item_ids[0],'uncertain')
        app.store.resolve_many([batch.item_ids[0]],'main',False)
        app.reload_rows()
        panel=app.exception_panel
        panel.tree.selection_set(str(batch.item_ids[0]))
        monkeypatch.setattr('xhs_importer.exception_panel.messagebox.showinfo',lambda *a,**k:None)
        monkeypatch.setattr(app,'launch_import',lambda *a,**k:pytest.fail('sample must use bounded trial mode'))
        panel.start_retry()
        assert app.setup_panel.steps.index(app.setup_panel.steps.select())==2
        monkeypatch.setattr('xhs_importer.gui.filedialog.askopenfilename',lambda **k:batch.rows(app.store)[0]['path'])
        app.trial_selected_file()
        assert '自动测试图' in app.trial_status.get()
    finally:
        app.close()
