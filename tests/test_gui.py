import time
import tkinter as tk
from PIL import Image
from xhs_importer.gui import Application
from xhs_importer.profile import Profile, FlowStep


def test_offline_gui_scans_and_recovers_persisted_tasks(tmp_path, monkeypatch):
    folder = tmp_path / '表情'
    folder.mkdir()
    Image.new('RGB', (22, 33), 'red').save(folder / '你好.png')
    root = tk.Tk()
    root.withdraw()
    app = Application(root, tmp_path / 'data')
    try:
        assert not app.profile.tested
        app.account.set('测试账号')
        app.folder.set(str(folder))
        app.scan()
        deadline = time.monotonic() + 10
        while app.busy and time.monotonic() < deadline:
            root.update()
            time.sleep(0.02)
        assert not app.busy
        assert len(app.rows_cache) == 1
        row = next(iter(app.rows_cache.values()))
        assert row['status'] == 'pending'
        assert (row['width'], row['height']) == (22, 33)
        app.serial.set('emulator-1')
        app.confirmed.set(True)
        app.auto.set(True)
        app.serial.set('emulator-2')
        assert not app.confirmed.get(), 'Changing the emulator must require account confirmation'
        assert not app.auto.get()
        app.confirmed.set(True)
        app.adb.set('D:/another/adb.exe')
        assert not app.confirmed.get(), 'Changing ADB must invalidate the confirmed connection'
        app.profile = Profile(package='app.xhs', version='2', favorites={'text': 'favorites'},
            open_steps=[FlowStep('click', {'text': 'add'}, package='app.xhs')],
            pick_steps=[FlowStep('target', {'text': '{filename}'}, package='app.xhs')],
            submit_steps=[FlowStep('click', {'text': 'ok'}, package='app.xhs')])
        app.store.set_status(row['id'], 'success', 'Historical success from an older flow')
        app.confirmed.set(True)
        app.test_formats['PNG'].set(True)
        app.sync_checked.set(True)
        notices = []
        monkeypatch.setattr('xhs_importer.gui.messagebox.showinfo', lambda *args, **kwargs: notices.append(args))
        app.mark_tested()
        assert not app.profile.tested, 'Historical success must not certify a changed profile'
        assert notices
        app.account.set('account-b')
        assert len(app.rows_cache) == 0
        assert not app.confirmed.get()
    finally:
        app.close()
