from pathlib import Path
import tkinter as tk

from PIL import Image

from xhs_importer.config import Settings
from xhs_importer.gui import Application
from xhs_importer.wechat_panel import choose_source
from xhs_importer.wechat_pipeline import queue_export
from xhs_importer.wechat_export import ExportResult
from xhs_importer.store import Store


def test_choose_source_never_guesses_between_multiple_accounts(tmp_path):
    a, b = tmp_path / 'a/emoticon.db', tmp_path / 'b/emoticon.db'
    assert choose_source([a, b], '') == ''
    assert choose_source([a, b], str(b)) == str(b)
    assert choose_source([a], '') == str(a)
    assert choose_source([], str(b)) == ''


def test_preview_only_uses_frozen_source_target_and_populates_existing_queue(tmp_path, monkeypatch):
    source = tmp_path / 'emoticon.db'
    source.touch()
    image = tmp_path / 'sample.png'
    Image.new('RGB', (20, 20), 'red').save(image)
    Settings(account='main').save(tmp_path / 'settings.json')
    root = tk.Tk()
    root.withdraw()
    app = Application(root, tmp_path)
    captured = {}
    callbacks = []
    monkeypatch.setattr(app, 'background', lambda task, callback=None: callbacks.append((task, callback)))
    def fake_pipeline(db, directory, account, formats, stop, notify, import_fn=None):
        captured.update(source=db, account=account, import_fn=import_fn)
        store = Store(directory / 'queue.sqlite3')
        try:
            batch = queue_export(store, account, [image], formats, stop)
        finally:
            store.close()
        return ExportResult(paths=[image], exported=1), batch
    monkeypatch.setattr('xhs_importer.wechat_panel.run_wechat_pipeline', fake_pipeline)
    try:
        panel = app.wechat_panel
        panel.source.set(str(source))
        panel.confirmed.set(True)
        panel.start(False)
        assert callbacks
        task, done = callbacks[-1]
        app.account.set('changed-after-dispatch')
        panel.source.set('other')
        result = task()
        assert captured == {'source': source, 'account': 'main', 'import_fn': None}
        assert app.store.rows('main')[0]['status'] == 'pending'
        done(result)
    finally:
        app.close()


def test_wechat_primary_buttons_visible_at_minimum_window(tmp_path):
    root = tk.Tk()
    app = Application(root, tmp_path)
    try:
        root.geometry('1060x800')
        app.tabs.select(app.wechat_tab)
        root.update()
        for button in app.wechat_panel.primary_buttons:
            assert button.winfo_ismapped()
            assert button.winfo_height() > 10
            assert button.winfo_rootx() + button.winfo_width() <= root.winfo_rootx() + root.winfo_width()
            assert button.winfo_rooty() + button.winfo_height() <= root.winfo_rooty() + root.winfo_height()
    finally:
        app.close()


def test_home_primary_routes_missing_setup_before_importing(tmp_path, monkeypatch):
    from xhs_importer.catalog import inspect_file
    from xhs_importer.xhs_adapter import make_profile
    root = tk.Tk()
    root.withdraw()
    app = Application(root, tmp_path)
    routes, starts = [], []
    monkeypatch.setattr(app, 'show_setup', lambda step=0: routes.append(step), raising=False)
    panel = app.wechat_panel
    monkeypatch.setattr(panel, 'start', lambda import_after: starts.append(import_after))
    try:
        panel.primary_buttons[0].invoke()
        assert routes == [0] and not starts
        app.adb.set('test-adb')
        app.serial.set('test-device')
        panel.primary_buttons[0].invoke()
        assert routes == [0, 1] and not starts
        source = tmp_path / 'emoticon.db'
        source.touch()
        app.account.set('main')
        panel.source.set(str(source))
        app.confirmed.set(True)
        panel.primary_buttons[0].invoke()
        assert routes[-1] == 2 and not starts

        app.profile = make_profile('9.33.4')
        app.profile.tested = app.profile.phone_sync = True
        app.profile.tested_formats = ['PNG']
        app.profile.tested_context = app.profile_context()
        panel.refresh_home()
        panel.primary_buttons[0].invoke()
        assert not starts, 'Profile flags without actual trial proof must not show ready'
        image = tmp_path / 'proof.png'
        Image.new('RGB', (20, 20), 'red').save(image)
        app.store.add('main', [inspect_file(image)])
        item_id = app.store.rows('main')[0]['id']
        app.store.begin_attempt(item_id, app.profile_context(), 'trial')
        app.store.set_status(item_id, 'submitting')
        app.store.record_visual(item_id)
        app.store.set_status(item_id, 'success')
        panel.refresh_home()
        panel.primary_buttons[0].invoke()
        assert starts == [True]
        assert str(source) not in panel.summary.get()
        app.serial.set('different-device')
        panel.refresh_home()
        panel.primary_buttons[0].invoke()
        assert starts == [True], 'Changing device must revoke the ready state'
    finally:
        app.close()


def test_home_preview_and_source_change_share_account_confirmation(tmp_path, monkeypatch):
    root = tk.Tk()
    root.withdraw()
    app = Application(root, tmp_path)
    routes, starts = [], []
    panel = app.wechat_panel
    monkeypatch.setattr(app, 'show_setup', lambda step=0: routes.append(step), raising=False)
    monkeypatch.setattr(panel, 'start', lambda import_after: starts.append(import_after))
    try:
        assert panel.confirmed is app.confirmed
        panel.preview()
        assert routes == [1] and not starts
        source = tmp_path / 'emoticon.db'
        source.touch()
        app.account.set('main')
        panel.source.set(str(source))
        app.confirmed.set(True)
        panel.preview()
        assert starts == [False], 'Preview must work without an emulator or qualification'
        panel.source.set(str(tmp_path / 'another/emoticon.db'))
        assert not app.confirmed.get()
        assert not app.profile.tested
    finally:
        app.close()


def test_ready_home_requires_session_confirmation_without_setup_navigation(tmp_path):
    from types import SimpleNamespace
    from xhs_importer.wechat_panel import WechatPanel
    from xhs_importer.xhs_adapter import make_profile
    root = tk.Tk()
    root.withdraw()
    source = tmp_path / 'emoticon.db'
    source.touch()
    settings = Settings(account='main', adb='test-adb', serial='test-device')
    profile = make_profile('9.33.4', visual=False)
    profile.result = {'text': '添加成功'}
    profile.tested = profile.phone_sync = True
    profile.tested_formats = ['PNG']
    profile.tested_context = profile.verification_context('main', 'test-device', 'test-adb')
    routes, imports = [], []
    panel = WechatPanel.__new__(WechatPanel)
    panel.app = SimpleNamespace(busy=False, current_settings=lambda: settings, profile=profile,
        account=tk.StringVar(value='main'), show_setup=lambda step: routes.append(step))
    panel.source = tk.StringVar(value=str(source))
    panel.confirmed = tk.BooleanVar(value=False)
    panel.readiness = tk.StringVar()
    panel.start = lambda auto: imports.append(auto)
    try:
        panel.primary_action()
        panel.preview()
        assert not routes and not imports
        assert panel.readiness.get(), 'Show the missing confirmation on the same home page'
        panel.confirmed.set(True)
        panel.primary_action()
        panel.preview()
        assert imports == [True, False] and not routes
    finally:
        root.destroy()


def test_upgrade_home_loads_saved_visual_qualification_before_store_is_ready(tmp_path):
    import json
    from xhs_importer.catalog import inspect_file
    from xhs_importer.xhs_adapter import make_profile
    source = tmp_path / 'emoticon.db'
    source.touch()
    (tmp_path / 'wechat-source.json').write_text(json.dumps({'database': str(source)}))
    Settings(account='main', adb='test-adb', serial='test-device').save(tmp_path / 'settings.json')
    profile = make_profile('9.33.4')
    profile.tested = profile.phone_sync = True
    profile.tested_formats = ['PNG']
    profile.tested_context = profile.verification_context('main', 'test-device', 'test-adb')
    profile.save(tmp_path / 'profile.json')
    image = tmp_path / 'proof.png'
    Image.new('RGB', (20, 20), 'red').save(image)
    store = Store(tmp_path / 'queue.sqlite3')
    store.add('main', [inspect_file(image)])
    item_id = store.rows('main')[0]['id']
    store.begin_attempt(item_id, profile.tested_context, 'trial')
    store.set_status(item_id, 'submitting')
    store.record_visual(item_id)
    store.set_status(item_id, 'success')
    store.close()
    root = tk.Tk()
    root.withdraw()
    app = None
    try:
        app = Application(root, tmp_path)
        assert app.wechat_panel.setup_needed()[0] is None
        assert app.profile.revision == profile.revision
        assert app.profile.tested and app.profile.tested_formats == ['PNG']
        assert not app.confirmed.get(), 'A saved source never confirms current login for the user'
    finally:
        if app is not None:
            app.close()
        else:
            root.destroy()


def test_manual_confirmation_cleans_calibration_copy_without_manifest(tmp_path, monkeypatch):
    from test_staging_cleanup import MediaAdb, TOKEN
    from xhs_importer.catalog import inspect_file
    image = tmp_path / 'sample.png'
    Image.new('RGB', (20, 20), 'red').save(image)
    Settings(adb='test-adb', serial='device', account='main').save(tmp_path / 'settings.json')
    root = tk.Tk()
    root.withdraw()
    app = Application(root, tmp_path)
    callbacks = []
    adb = MediaAdb()
    monkeypatch.setattr(app, 'background', lambda task, callback=None, after_idle=None: callbacks.append(task))
    monkeypatch.setattr('xhs_importer.gui.Adb', lambda _: adb)
    try:
        app.store.add('main', [inspect_file(image)])
        row = app.store.rows('main')[0]
        app.store.reserve_calibration(row['id'], 'main')
        adb.digest = row['digest']
        adb.remote = f'/sdcard/DCIM/XHSImport_{TOKEN}/{row["digest"][:20]}_{TOKEN}.png'
        app.reload_rows()
        app.tree.selection_set(str(row['id']))
        app.resolve(True)
        assert app.store.rows('main')[0]['status'] == 'success'
        assert len(callbacks) == 1
        assert callbacks[0]() == 1
        assert not adb.file and not adb.index
    finally:
        app.close()


def test_current_blocked_notice_survives_idle_and_old_failure_record(tmp_path):
    from xhs_importer.catalog import inspect_file
    image = tmp_path / 'sample.png'
    Image.new('RGB', (20, 20), 'red').save(image)
    Settings(account='main').save(tmp_path / 'settings.json')
    root = tk.Tk()
    root.withdraw()
    app = Application(root, tmp_path)
    try:
        app.store.add('main', [inspect_file(image)])
        row = app.store.rows('main')[0]
        app.store.set_status(row['id'], 'failed', '旧的超时记录')
        app.reload_rows()
        app.tree.selection_set(str(row['id']))
        notice = '尚未开始：请打开收藏面板后再点击导入。'
        app.events.put(('blocked', notice))
        app.events.put(('idle', None))
        root.after_cancel(app.poll_id)
        app.poll()
        assert app.action_notice.get() == notice
        assert '上次任务记录：旧的超时记录' in app.detail.get()
        app.events.put(('update', '正在处理：下一张.gif'))
        root.after_cancel(app.poll_id)
        app.poll()
        assert app.action_notice.get() == '正在处理：下一张.gif'
    finally:
        app.close()
