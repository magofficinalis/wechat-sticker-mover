import tkinter as tk
from tkinter import ttk
import time
import pytest
from PIL import Image
from xhs_importer.gui import Application
from xhs_importer.config import Settings
from xhs_importer.catalog import inspect_file
from xhs_importer.xhs_adapter import make_profile


def make_app(tmp_path):
    Settings(adb='test-adb', serial='test', account='main').save(tmp_path/'settings.json')
    make_profile('9.33.4').save(tmp_path/'profile.json')
    root=tk.Tk()
    root.withdraw()
    return Application(root,tmp_path)


def test_one_global_pause_is_reachable_across_pages_and_preserves_progress(tmp_path):
    app=make_app(tmp_path)
    try:
        app.root.deiconify()
        app.root.geometry('1060x800')
        def buttons(widget):
            return ([widget] if isinstance(widget,ttk.Button) else []) + [
                child for w in widget.winfo_children() for child in buttons(w)]
        pauses=[w for w in buttons(app.root) if '暂停' in str(w['text'])]
        assert pauses == [app.pause_button]
        assert app.pause_button.instate(['disabled'])
        before=app.store.rows('main')
        app.set_busy(True)
        for tab in app.tabs.tabs():
            app.tabs.select(tab)
            app.root.update()
            assert app.pause_button.winfo_ismapped()
            assert not app.pause_button.instate(['disabled'])
        app.show_setup(2)
        app.root.update()
        app.pause_button.invoke()
        assert app.stop_event.is_set()
        assert app.pause_button.instate(['disabled'])
        assert str(app.pause_button['text']) == '正在暂停…'
        assert app.store.rows('main') == before
        app.set_busy(False)
        assert app.pause_button.instate(['disabled'])
        assert str(app.pause_button['text']) == '暂停任务'
    finally:
        app.set_busy(False)
        app.close()


def test_product_has_four_pages_and_reachable_setup_steps(tmp_path):
    app=make_app(tmp_path)
    try:
        assert [app.tabs.tab(t,'text').split('（')[0] for t in app.tabs.tabs()] == ['开始搬运','我的表情','异常处理','设置与帮助']
        for index in range(3):
            app.show_setup(index)
            assert app.tabs.select()==str(app.settings_tab)
            assert app.setup_panel.steps.index(app.setup_panel.steps.select())==index
        assert app.wechat_panel.confirmed is app.confirmed
        app.confirmed.set(True)
        app.wechat_panel.source.set('another/emoticon.db')
        assert not app.confirmed.get()
    finally:
        app.close()


def test_prepare_existing_profile_preserves_qualification(tmp_path, monkeypatch):
    app=make_app(tmp_path)
    try:
        app.profile.tested=True
        app.profile.phone_sync=True
        app.profile.tested_formats=['PNG']
        app.profile.tested_context=app.profile_context()
        app.profile.save(tmp_path/'profile.json')
        before=app.profile.revision
        class Device:
            def identity(self, package): return {'version':'9.33.4','serial':'test'}
        monkeypatch.setattr(app,'make_device',lambda:Device())
        def run(task, callback=None):
            result=task()
            if callback: callback(result)
        monkeypatch.setattr(app,'background',run)
        app.prepare_profile()
        assert app.profile.revision==before
        assert app.profile.tested_formats==['PNG'] and app.profile.tested
    finally:
        app.close()


@pytest.mark.parametrize('visual,change_context',[(True,False),(False,False),(True,True)])
def test_trial_confirmation_requires_current_attempt_and_visual_proof(tmp_path, monkeypatch, visual, change_context):
    app=make_app(tmp_path)
    monkeypatch.setattr(app,'cleanup_confirmed',lambda **kw:None)
    monkeypatch.setattr('xhs_importer.gui.messagebox.showinfo',lambda *a,**kw:None)
    try:
        path=tmp_path/'new.png'
        Image.new('RGB',(20,20),'red').save(path)
        app.store.add('main',[inspect_file(path)])
        row=app.store.rows('main')[0]
        context=app.profile_context()
        app.store.begin_attempt(row['id'],context)
        app.store.set_status(row['id'],'submitting')
        if visual: app.store.record_visual(row['id'])
        app.store.set_status(row['id'],'uncertain')
        app.trial_item_id,app.trial_context=row['id'],context
        app.confirmed.set(True)
        if change_context:
            app.serial.set('changed')
            app.confirmed.set(True)
        app.confirm_trial_sync()
        assert app.profile.tested == (visual and not change_context)
        assert app.store.rows('main')[0]['status']==('uncertain' if change_context else 'success')
    finally:
        app.close()


def test_local_primary_does_not_silently_import_unverified_batch(tmp_path, monkeypatch):
    app=make_app(tmp_path)
    try:
        for name,color in [('one','red'),('two','blue')]:
            path=tmp_path/f'{name}.png'
            Image.new('RGB',(20,20),color).save(path)
            app.store.add('main',[inspect_file(path)])
        app.reload_rows()
        app.tree.selection_set(app.tree.get_children())
        app.confirmed.set(True)
        calls=[]
        monkeypatch.setattr(app,'start',lambda *a,**kw:calls.append((a,kw)))
        app.start_product_import()
        assert not calls
        assert app.tabs.select()==str(app.settings_tab)
    finally:
        app.close()


def test_guided_trial_runs_real_engine_then_user_confirms_sync(tmp_path, monkeypatch):
    from test_visual_engine import VisualPhone
    app=make_app(tmp_path)
    try:
        path=tmp_path/'new-sticker.png'
        Image.new('RGB',(20,20),'blue').save(path)
        app.confirmed.set(True)
        monkeypatch.setattr('xhs_importer.gui.filedialog.askopenfilename',lambda **kw:str(path))
        monkeypatch.setattr(app,'make_device',lambda:VisualPhone())
        monkeypatch.setattr(app,'cleanup_confirmed',lambda **kw:None)
        app.trial_selected_file()
        deadline=time.monotonic()+8
        while app.busy and time.monotonic()<deadline:
            app.root.update()
            time.sleep(.01)
        assert not app.busy
        assert app.current_trial()['status']=='uncertain'
        assert app.current_trial()['attempt_visual']==1
        assert not app.profile.tested
        app.confirm_trial_sync()
        assert app.profile.tested_formats==['PNG']
        assert app.store.rows('main')[0]['status']=='success'
    finally:
        app.close()


def test_current_trial_cannot_borrow_old_visual_proof(tmp_path, monkeypatch):
    app=make_app(tmp_path)
    monkeypatch.setattr(app,'cleanup_confirmed',lambda **kw:None)
    try:
        context=app.profile_context()
        ids=[]
        for name,color in [('old','red'),('current','blue')]:
            path=tmp_path/f'{name}.png'
            Image.new('RGB',(20,20),color).save(path)
            app.store.add('main',[inspect_file(path)])
            row=app.store.rows('main')[-1]
            ids.append(row['id'])
            app.store.begin_attempt(row['id'],context)
            app.store.set_status(row['id'],'submitting')
            if name=='old': app.store.record_visual(row['id'])
            app.store.set_status(row['id'],'uncertain')
            if name=='old': app.store.resolve(row['id'],True)
        app.trial_item_id,app.trial_context=ids[-1],context
        app.confirmed.set(True)
        app.confirm_trial_sync()
        assert not app.profile.tested
        assert app.store.rows('main')[-1]['status']=='success'
    finally:
        app.close()


def test_setup_actions_reachable_with_large_fonts(tmp_path):
    app=make_app(tmp_path)
    try:
        app.root.deiconify()
        app.root.geometry('1060x800')
        style=ttk.Style(app.root)
        style.configure('.',font=('Microsoft YaHei UI',14))
        style.configure('Accent.TButton',font=('Microsoft YaHei UI',14,'bold'))
        app.show_setup(2)
        app.root.update()
        buttons=[w for w in app.profile_tab.winfo_children() if isinstance(w,ttk.Button)]
        assert all(b.winfo_ismapped() and b.winfo_height()>=b.winfo_reqheight() for b in buttons)
        scroller=app.setup_panel.verification_scroller
        assert scroller.canvas.yview()[1]<1
        scroller.canvas.yview_moveto(1)
        app.root.update()
        for b in (app.trial_panel.confirm_button,buttons[-1]):
            assert b.winfo_rooty()>=scroller.canvas.winfo_rooty()
            assert b.winfo_rooty()+b.winfo_height()<=scroller.canvas.winfo_rooty()+scroller.canvas.winfo_height()
    finally:
        app.close()


def test_select_pending_only_current_account_without_starting_import(tmp_path, monkeypatch):
    app=make_app(tmp_path)
    try:
        for index, state in enumerate(['pending','success','failed','skipped','uncertain','pending']):
            path=tmp_path/f'{index}.png'
            Image.new('RGB',(20,20),(index*30,30,60)).save(path)
            app.store.add('main',[inspect_file(path)])
            app.store.set_status(app.store.rows('main')[-1]['id'],state)
        rows=app.store.rows('main')
        app.store.db.execute('UPDATE items SET recovery_queued=1 WHERE id=?',(rows[-1]['id'],))
        app.store.db.commit()
        app.store.add('another',[inspect_file(tmp_path/'0.png')])
        app.reload_rows()
        monkeypatch.setattr(app,'start',lambda *a,**k:pytest.fail('selection must not submit'))
        before=app.store.rows('main')
        app.select_all_pending()
        assert app.tree.selection()==(str(rows[0]['id']),)
        assert app.store.rows('main')==before
        assert '1 张' in app.action_notice.get()
        app.store.set_status(rows[0]['id'],'success')
        app.reload_rows()
        app.select_all_pending()
        assert not app.tree.selection()
    finally:
        app.close()


def test_selected_batch_progress_stays_stable_and_counts_only_success(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    try:
        for name, color in [('a.png', 'red'), ('b.png', 'blue'), ('c.png', 'green')]:
            path = tmp_path / name
            Image.new('RGB', (20, 20), color).save(path)
            app.store.add('main', [inspect_file(path)])
        rows = app.store.rows('main')
        app.reload_rows()
        monkeypatch.setattr(app, 'background', lambda *args, **kwargs: None)
        app.launch_import(app.current_settings(), object(), app.profile, None, True,
                          item_ids=[rows[0]['id'], rows[1]['id']], track_batch=True)
        app.tree.selection_set(str(rows[2]['id']))
        app.store.set_status(rows[0]['id'], 'success')
        app.store.set_status(rows[1]['id'], 'skipped')
        app.reload_rows()
        assert app.batch_progress.ids == frozenset((rows[0]['id'], rows[1]['id']))
        assert app.batch_bar['value'] == 50
        assert '已收藏 1 / 2' in app.batch_text.get()
        assert '未成功 1' in app.batch_text.get() and '跳过 1' in app.batch_text.get()
        app.account.set('other')
        app.root.update()
        assert not app.batch_frame.winfo_ismapped()
    finally:
        app.close()


def test_trial_or_exception_launch_does_not_replace_selected_batch_progress(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    try:
        path = tmp_path / 'a.png'
        Image.new('RGB', (20, 20), 'red').save(path)
        app.store.add('main', [inspect_file(path)])
        selected = app.store.rows('main')[0]['id']
        app.reload_rows()
        monkeypatch.setattr(app, 'background', lambda *args, **kwargs: None)
        app.launch_import(app.current_settings(), object(), app.profile, None, True,
                          item_ids=[selected], track_batch=True)
        previous = app.batch_progress
        app.launch_import(app.current_settings(), object(), app.profile, 1, False, item_id=selected)
        assert app.batch_progress is previous
    finally:
        app.close()


def test_gallery_click_multiselect_and_list_view_share_the_same_selection(tmp_path):
    app = make_app(tmp_path)
    try:
        for name, color in [('a.png', 'red'), ('b.png', 'blue'), ('c.png', 'green')]:
            path = tmp_path / name
            Image.new('RGB', (30, 30), color).save(path)
            app.store.add('main', [inspect_file(path)])
        before = app.store.rows('main')
        app.reload_rows()
        app.root.deiconify()
        app.tabs.select(app.import_tab)
        app.show_import_view('grid')
        app.root.update()
        ids = list(app.rows_cache)
        app.gallery.toggle_item(ids[0])
        app.gallery.toggle_item(ids[2])
        app.root.update()
        assert set(app.tree.selection()) == {ids[0], ids[2]}
        app.show_import_view('list')
        app.root.update()
        assert set(app.tree.selection()) == {ids[0], ids[2]}
        app.tree.selection_set(ids[1])
        app.tree.focus(ids[1])
        app.show_import_view('grid')
        app.root.update()
        assert app.gallery.selected_ids() == {ids[1]}
        app.gallery.toggle_item(ids[1])
        assert not app.tree.selection()
        assert app.store.rows('main') == before
    finally:
        app.close()


def test_gallery_select_all_uses_existing_pending_rules(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    try:
        for name, color in [('a.png', 'red'), ('b.png', 'blue')]:
            path = tmp_path / name
            Image.new('RGB', (20, 20), color).save(path)
            app.store.add('main', [inspect_file(path)])
        rows = app.store.rows('main')
        app.store.set_status(rows[1]['id'], 'success')
        app.reload_rows()
        monkeypatch.setattr(app, 'start', lambda *a, **kw: pytest.fail('selection must not import'))
        app.show_import_view('grid')
        app.select_all_pending()
        assert app.gallery.selected_ids() == {str(rows[0]['id'])}
        assert app.store.rows('main')[1]['status'] == 'success'
    finally:
        app.close()


@pytest.mark.parametrize('selected_only', [True, False])
def test_mixed_formats_import_verified_subset_without_setup_redirect(tmp_path, monkeypatch, selected_only):
    app=make_app(tmp_path)
    try:
        for name,color in [('ready.png','red'),('later.webp','blue'),('recovery.png','green')]:
            path=tmp_path/name
            Image.new('RGB',(20,20),color).save(path)
            app.store.add('main',[inspect_file(path)])
        rows=app.store.rows('main')
        app.store.db.execute('UPDATE items SET recovery_queued=1 WHERE id=?',(rows[-1]['id'],))
        app.store.db.commit()
        app.profile.tested=True
        app.profile.phone_sync=True
        app.profile.tested_formats=['PNG']
        app.profile.tested_context=app.profile_context()
        app.profile.save(tmp_path/'profile.json')
        app.confirmed.set(True)
        app.reload_rows()
        app.tabs.select(app.import_tab)
        app.select_all_pending()
        launches=[]
        monkeypatch.setattr(app,'make_device',lambda:object())
        monkeypatch.setattr(app,'launch_import',lambda *a,**kw:launches.append((a,kw)))
        before=app.store.rows('main')
        app.start_product_import(selected_only)
        assert app.tabs.select()==str(app.import_tab)
        assert len(launches)==1
        assert launches[0][1]['item_ids']==[rows[0]['id']]
        assert launches[0][1]['track_batch'] is True
        assert launches[0][0][4] is True
        assert app.tree.selection()==(str(rows[0]['id']),)
        assert app.store.rows('main')==before
        assert app.profile.tested_formats==['PNG']
        assert 'WEBP' in app.selection_notice.get() and '1 张' in app.selection_notice.get()
    finally:
        app.close()


@pytest.mark.parametrize('problem', ['format','context','device','qualification'])
def test_product_import_explains_exact_verification_block(tmp_path, monkeypatch, problem):
    app=make_app(tmp_path)
    try:
        path=tmp_path/'not-yet.webp'
        Image.new('RGB',(20,20),'blue').save(path)
        app.store.add('main',[inspect_file(path)])
        app.profile.tested=True
        app.profile.phone_sync=True
        app.profile.tested_formats=['PNG']
        app.profile.tested_context=app.profile_context()
        if problem=='context':
            app.serial.set('another-device')
        elif problem=='device':
            app.serial.set('')
        elif problem=='qualification':
            app.profile.tested=False
        app.confirmed.set(True)
        app.reload_rows()
        app.select_all_pending()
        before=app.store.rows('main')
        monkeypatch.setattr(app,'start',lambda *a,**kw:pytest.fail('blocked batch must not start'))
        app.start_product_import()
        assert app.tabs.select()==str(app.settings_tab)
        assert app.setup_panel.steps.index(app.setup_panel.steps.select())==(0 if problem=='device' else 2)
        reason=app.status.get()
        expected={'format':'WEBP','context':'账号、设备或界面配置已变化',
                  'device':'请先连接并选择模拟器','qualification':'单张实测和手机同步核对'}[problem]
        assert expected in reason
        if problem!='device':
            assert expected in app.trial_status.get()
        assert app.store.rows('main')==before
    finally:
        app.close()
