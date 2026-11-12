import sys
import time
import tkinter as tk
from tkinter import ttk

from PIL import Image

from xhs_importer.catalog import scan_folder
from xhs_importer.config import Settings
from xhs_importer.gui import Application
from xhs_importer.store import Store
from xhs_importer.xhs_adapter import make_profile


def application(tmp_path):
    folder = tmp_path / 'images'
    folder.mkdir()
    for i, color in enumerate(('red','green','blue','yellow','purple')):
        Image.new('RGB', (30,30), color).save(folder / f'{i}.png')
    Settings(account='main', adb=sys.executable, serial='test', folder=str(folder)).save(tmp_path/'settings.json')
    make_profile('9.33.4').save(tmp_path/'profile.json')
    store = Store(tmp_path/'queue.sqlite3')
    store.add('main', scan_folder(folder))
    ids = [r['id'] for r in store.rows('main')]
    for item_id, state in zip(ids, ('failed','skipped','uncertain','pending','success')):
        store.set_status(item_id, state, f'problem {item_id}')
    store.close()
    root = tk.Tk()
    root.withdraw()
    return Application(root, tmp_path), ids


def spin(root, condition):
    deadline = time.monotonic()+5
    while time.monotonic()<deadline and not condition():
        root.update()
        time.sleep(.01)
    assert condition()


def select(panel, ids):
    panel.tree.selection_set([str(i) for i in ids])
    if ids:
        panel.tree.focus(str(ids[0]))
    panel.row_detail()


def test_exception_filters_preview_and_hidden_selection_are_isolated(tmp_path):
    app, ids = application(tmp_path)
    try:
        panel = app.exception_panel
        assert set(panel.rows) == {str(i) for i in ids[:3]}
        spin(app.root, lambda: all(panel.tree.item(i, 'image') for i in panel.rows))
        panel.search.set('1.png')
        assert tuple(panel.rows) == (str(ids[1]),)
        panel.select_all()
        assert panel.tree.selection() == (str(ids[1]),)
        panel.search.set('')
        panel.filter.set('失败')
        assert not panel.tree.selection()
        assert tuple(panel.rows) == (str(ids[0]),)
        select(panel, [ids[0]])
        assert 'problem' in panel.detail.get('1.0', 'end')
        app.serial.set('another-device')
        assert not panel.tree.selection()
        app.account.set('other')
        assert not panel.rows and not panel.tree.selection()
        assert not panel.images.frames
    finally:
        app.close()


def test_bulk_requeue_and_busy_guard_preserve_unselected_tasks(tmp_path):
    app, ids = application(tmp_path)
    try:
        panel = app.exception_panel
        select(panel, ids[:2])
        app.set_busy(True)
        panel.resolve_selected(False)
        assert app.store.rows('main')[0]['status'] == 'failed'
        app.set_busy(False)
        panel.resolve_selected(False)
        assert panel.filter.get() == '待重试'
        assert set(panel.rows) == {str(i) for i in ids[:2]}
        assert app.store.rows('main')[3]['recovery_queued'] == 0
        panel.park_selected()
        assert all(r['status']=='skipped' for r in app.store.rows('main')[:2])
        assert not panel.rows
    finally:
        app.set_busy(False)
        app.close()


def test_retry_dispatch_uses_selected_queue_and_frozen_account(tmp_path, monkeypatch):
    from test_visual_engine import VisualPhone
    app, ids = application(tmp_path)
    try:
        app.store.resolve(ids[2], True)
        app.store.resolve_many(ids[:2], 'main', False)
        app.reload_rows()
        app.confirmed.set(True)
        panel = app.exception_panel
        panel.filter.set('待重试')
        select(panel, [ids[1]])
        phone = VisualPhone()
        monkeypatch.setattr(app, 'make_device', lambda: phone)
        jobs = []
        monkeypatch.setattr(app, 'background', lambda task, callback=None: jobs.append(task))
        panel.start_retry()
        assert len(jobs) == 1
        app.account.set('other')
        jobs[0]()
        rows = app.store.rows('main')
        assert [r['status'] for r in rows] == ['pending','uncertain','success','pending','success']
        assert sum(s.get('text')=='添加' for s in phone.clicks) == 1
    finally:
        app.close()


def test_retry_shows_blocked_reason_on_exception_page(tmp_path, monkeypatch):
    from xhs_importer.engine import ImportEngine
    app, ids = application(tmp_path)
    try:
        app.store.resolve_many([ids[0]], 'main', False)
        app.reload_rows()
        app.confirmed.set(True)
        panel = app.exception_panel
        select(panel, [ids[0]])
        monkeypatch.setattr(app, 'make_device', lambda: object())
        def blocked(engine, *args, **kwargs):
            engine.notify('blocked', '尚未开始导入：请打开收藏面板。')
        monkeypatch.setattr(ImportEngine, 'run', blocked)
        panel.retry_button.invoke()
        spin(app.root, lambda: not app.busy)
        assert '请打开收藏面板' in panel.retry_notice.get()
        assert app.store.rows('main')[0]['status'] == 'pending'
    finally:
        app.close()


def test_retry_reports_device_setup_error_without_changing_queue(tmp_path, monkeypatch):
    app, ids = application(tmp_path)
    try:
        app.store.resolve_many([ids[0]], 'main', False)
        app.reload_rows()
        app.confirmed.set(True)
        panel = app.exception_panel
        select(panel, [ids[0]])
        def fail():
            raise ValueError('模拟器不可用')
        monkeypatch.setattr(app, 'make_device', fail)
        panel.retry_button.invoke()
        assert '模拟器不可用' in panel.retry_notice.get()
        assert app.store.rows('main')[0]['status'] == 'pending'
    finally:
        app.close()


def test_confirm_one_and_continue_waits_for_cleanup_and_respects_stop(tmp_path, monkeypatch):
    app, ids = application(tmp_path)
    try:
        app.store.resolve_many(ids[:3], 'main', False)
        app.reload_rows()
        app.confirmed.set(True)
        panel = app.exception_panel
        select(panel, ids[:2])
        starts = []
        monkeypatch.setattr(app, 'make_device', lambda: object())
        monkeypatch.setattr(app, 'launch_import', lambda *args, **kwargs: starts.append(kwargs['item_ids']))
        panel.start_retry()
        assert starts == [ids[:2]]
        starts.clear()
        app.store.set_status(ids[0], 'uncertain')
        app.reload_rows()
        select(panel, [ids[0]])
        callbacks, starts = [], []
        monkeypatch.setattr(app, 'cleanup_confirmed', lambda item_id=None, on_done=None: callbacks.append(on_done))
        panel.confirm_and_continue()
        assert app.store.rows('main')[0]['status'] == 'success'
        assert not starts and len(callbacks)==1
        app.stop_event.set()
        callbacks[0]()
        assert not starts
        app.stop_event.clear()
        callbacks[0]()
        assert starts == [[ids[1]]]
        assert app.store.rows('main')[2]['status'] == 'pending'
    finally:
        app.close()


def test_after_idle_callback_runs_after_controls_are_reenabled(tmp_path):
    app, _ = application(tmp_path)
    states=[]
    try:
        app.background(lambda: None, after_idle=lambda: states.append(app.busy))
        spin(app.root, lambda: bool(states))
        assert states == [False]
    finally:
        app.set_busy(False)
        app.close()


def test_scan_restored_source_refreshes_exception_preview(tmp_path):
    app, ids = application(tmp_path)
    try:
        panel = app.exception_panel
        source = tmp_path/'images/0.png'
        spin(app.root, lambda:not panel.images.jobs and not app.task_images.jobs)
        content = source.read_bytes()
        source.unlink()
        panel.images.refresh()
        app.reload_rows()
        select(panel, [ids[0]])
        spin(app.root, lambda: panel.tree.item(str(ids[0]), 'text')=='无预览')
        source.write_bytes(content)
        app.scan()
        spin(app.root, lambda:not app.busy)
        spin(app.root, lambda:bool(panel.tree.item(str(ids[0]), 'image')))
    finally:
        app.set_busy(False)
        app.close()


def test_exception_controls_fit_minimum_window(tmp_path):
    app, ids = application(tmp_path)
    app.root.deiconify()
    try:
        panel = app.exception_panel
        app.tabs.select(app.exception_tab)
        app.root.geometry('1060x800')
        app.root.update()
        app.store.resolve(ids[1], False)
        app.reload_rows()
        for selected in ([], [ids[0]], [ids[1]], [ids[2]]):
            select(panel, selected)
            app.root.update()
            for widget in panel.controls:
                if not widget.winfo_ismapped():
                    continue
                assert widget.winfo_height()>10
                assert widget.winfo_rootx()+widget.winfo_width()<=app.root.winfo_rootx()+app.root.winfo_width()
                assert widget.winfo_rooty()+widget.winfo_height()<=app.root.winfo_rooty()+app.root.winfo_height()
    finally:
        app.close()


def test_selecting_last_visible_row_keeps_it_visible_after_actions_appear(tmp_path):
    app,ids=application(tmp_path)
    try:
        app.store.set_status(ids[3],'skipped')
        app.reload_rows()
        app.root.deiconify()
        app.root.geometry('1060x800')
        app.tabs.select(app.exception_tab)
        app.root.update()
        panel=app.exception_panel
        panel.tree.see(str(ids[3]))
        app.root.update()
        assert panel.tree.bbox(str(ids[3]))
        select(panel,[ids[3]])
        app.root.update()
        box=panel.tree.bbox(str(ids[3]))
        assert box and box[1]+box[3]<=panel.tree.winfo_height()
    finally:
        app.close()


def test_exception_scrollbar_only_appears_for_real_overflow(tmp_path):
    app, ids = application(tmp_path)
    try:
        panel = app.exception_panel
        app.tabs.select(app.exception_tab)
        app.root.deiconify()
        app.root.geometry('1600x900')
        app.root.update()
        tree = panel.tree
        bar = next(w for w in tree.master.winfo_children()
                   if isinstance(w, ttk.Scrollbar) and str(w['orient'])=='horizontal')
        assert tree.xview() == (0.0, 1.0)
        assert not bar.winfo_ismapped()
        tree.column('reason', width=1800, stretch=False)
        app.root.update()
        assert bar.winfo_ismapped()
        # Exercise the command used by the scrollbar thumb's drag binding.
        app.root.tk.call(bar['command'], 'moveto', .5)
        app.root.update()
        position = tree.xview()[0]
        assert position > 0
        panel.reload()
        app.root.update()
        assert tree.xview()[0] == position
        tree.column('reason', width=150, stretch=True)
        app.root.update()
        assert tree.xview() == (0.0, 1.0)
        assert not bar.winfo_ismapped()
    finally:
        app.close()


def test_exception_actions_follow_selection_and_full_reason_stays_readable(tmp_path):
    app, ids = application(tmp_path)
    try:
        panel = app.exception_panel
        app.tabs.select(app.exception_tab)
        app.root.deiconify()
        app.root.update()
        def visible_buttons():
            app.root.update()
            return {str(w['text']) for w in panel.controls
                    if isinstance(w, ttk.Button) and w.winfo_ismapped()}
        assert '选中已收藏' not in visible_buttons()
        assert '选中未收藏，加入重试' not in visible_buttons()
        assert '重试选中' not in visible_buttons()
        long_reason = '未同时识别到新增成功提示和目标缩略图；' * 18 + '请在手机上核对是否收藏。'
        app.store.set_status(ids[0], 'failed', long_reason)
        app.reload_rows()
        select(panel, ids[:2])
        assert {'选中已收藏','选中未收藏，加入重试'} <= visible_buttons()
        assert '重试选中' not in visible_buttons()
        assert long_reason in panel.detail.get('1.0','end')
        assert panel.detail['wrap'] == 'word'
        select(panel, [ids[2]])
        assert '确认本张已收藏并继续' in visible_buttons()
        panel.mode.set('自动核对')
        assert '确认本张已收藏并继续' not in visible_buttons()
        panel.resolve_selected(False)
        assert '重试选中（1 张）' in visible_buttons()
        assert '选中已收藏' not in visible_buttons()
        assert '选中未收藏，加入重试' not in visible_buttons()
        app.set_busy(True)
        app.reload_rows()
        assert not any('暂停' in label for label in visible_buttons())
        assert str(app.pause_button['state'])!='disabled'
    finally:
        app.set_busy(False)
        app.close()


def test_single_retry_button_scopes_selection_and_whole_account_queue(tmp_path, monkeypatch):
    from xhs_importer.trial_batch import TrialBatch
    app, ids = application(tmp_path)
    try:
        app.store.resolve_many(ids[:2], 'main', False)
        sample = TrialBatch.create(tmp_path, app.store, 'main', app.profile_context(), ['GIF'])
        app.store.set_status(sample.item_ids[0], 'failed')
        app.store.resolve_many(sample.item_ids, 'main', False)
        app.store.add('other', scan_folder(tmp_path/'images'))
        other_id = app.store.rows('other')[0]['id']
        app.store.set_status(other_id, 'failed')
        app.store.resolve_many([other_id], 'other', False)
        app.reload_rows()
        app.confirmed.set(True)
        panel = app.exception_panel
        calls = []
        monkeypatch.setattr(app, 'make_device', lambda: object())
        monkeypatch.setattr(app, 'launch_import', lambda *a, **kw: calls.append(kw['item_ids']))
        select(panel, [ids[1]])
        assert str(panel.retry_button['text']) == '重试选中（1 张）'
        panel.retry_button.invoke()
        assert calls == [[ids[1]]]
        # Filtering clears the selection; explain that the whole account queue
        # will run, including eligible rows hidden by the filter.
        panel.filter.set('失败')
        assert not panel.tree.selection()
        assert str(panel.retry_button['text']) == '重试队列（2 张）'
        assert '不受筛选影响' in panel.selection_note.get()
        panel.retry_button.invoke()
        assert calls == [[ids[1]], ids[:2]]
        assert not hasattr(panel, 'queue_button')
    finally:
        app.close()


def test_ineligible_selected_retry_never_falls_back_to_whole_queue(tmp_path, monkeypatch):
    app, ids = application(tmp_path)
    try:
        app.store.resolve(ids[0], False)
        app.reload_rows()
        app.confirmed.set(True)
        panel = app.exception_panel
        calls, notices = [], []
        monkeypatch.setattr(app, 'launch_import', lambda *a, **kw: calls.append(kw))
        monkeypatch.setattr('xhs_importer.exception_panel.messagebox.showinfo', lambda *a, **kw: notices.append(a))
        for selected in ([ids[1]], ids[:2]):
            select(panel, selected)
            panel.retry_action()
        assert not calls and len(notices) == 2
        assert app.store.rows('main')[0]['status'] == 'pending'
    finally:
        app.close()
