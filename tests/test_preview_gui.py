import time
import tkinter as tk
from tkinter import ttk
import threading

from PIL import Image

from xhs_importer.catalog import scan_folder
from xhs_importer.config import Settings
from xhs_importer.gui import Application
from xhs_importer.store import Store
from xhs_importer.preview import Preview
from xhs_importer.preview_widgets import TaskImages


def wait_for(root, predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        root.update()
        if predicate():
            return
        time.sleep(0.01)
    assert predicate(), 'GUI condition timed out'


def test_real_tk_thumbnails_animation_selection_and_reload(tmp_path):
    source = tmp_path / 'images'
    source.mkdir()
    Image.new('RGB', (80, 40), 'red').save(source / 'one.png')
    Image.new('RGB', (50, 50), 'red').save(source / 'two.gif', save_all=True,
        append_images=[Image.new('RGB', (50, 50), 'blue')], duration=200, loop=0)
    Settings(account='main', folder=str(source)).save(tmp_path / 'settings.json')
    store = Store(tmp_path / 'queue.sqlite3')
    store.add('main', scan_folder(source))
    store.close()
    root = tk.Tk()
    root.withdraw()
    app = Application(root, tmp_path)
    try:
        assert app.tree.heading('#0')['text'] == '预览'
        ids = app.tree.get_children()
        wait_for(root, lambda: all(app.tree.item(iid, 'image') for iid in ids))
        app.tree.selection_set(ids[1])
        app.tree.focus(ids[1])
        wait_for(root, lambda: len(app.task_images.frames) == 2)
        assert app.task_images.animation_id is not None
        photo = app.task_images.picture.cget('image')
        wait_for(root, lambda: app.task_images.picture.cget('image') != photo)
        app.reload_rows()
        assert app.tree.selection() == (ids[1],)
        assert app.tree.item(ids[1], 'image')
        app.tree.selection_set(ids[0])
        app.tree.focus(ids[0])
        wait_for(root, lambda: len(app.task_images.frames) == 1)
        assert app.task_images.animation_id is None
        app.account.set('other')
        wait_for(root, lambda: not app.task_images.frames)
        assert not app.tree.get_children()
    finally:
        app.close()
    assert app.task_images.closed


def test_delayed_old_preview_cannot_replace_new_selection(tmp_path, monkeypatch):
    started, release = threading.Event(), threading.Event()
    def loader(path, digest):
        if path == 'old':
            started.set()
            release.wait(3)
        return Preview([Image.new('RGB', (200, 200), 'red')], [100], path)
    monkeypatch.setattr('xhs_importer.preview_widgets.load_preview', loader)
    root = tk.Tk()
    root.withdraw()
    images = TaskImages(root, ttk.Treeview(root), ttk.Frame(root))
    try:
        images.show({'path': 'old', 'digest': 'one'})
        assert started.wait(1)
        images.show({'path': 'new', 'digest': 'two'})
        release.set()
        wait_for(root, lambda: images.note.get() == 'new')
        assert images.name.get() == 'new'
        images.show(None)
        assert not images.frames
        assert images.animation_id is None
    finally:
        release.set()
        images.close()
        root.destroy()


def test_refresh_retries_missing_source_and_ignores_old_jobs(tmp_path):
    source = tmp_path / 'restored.png'
    Image.new('RGB', (20, 20), 'red').save(source)
    row = vars(scan_folder(tmp_path)[0]).copy()
    row['path'] = str(source)
    data = source.read_bytes()
    source.unlink()
    root = tk.Tk()
    root.withdraw()
    tree = ttk.Treeview(root)
    tree.insert('', 'end', iid='1')
    images = TaskImages(root, tree, ttk.Frame(root))
    try:
        images.update_rows({'1': row})
        images.show(row)
        wait_for(root, lambda: tree.item('1', 'text') == '无预览' and images.note.get())
        source.write_bytes(data)
        images.refresh()
        images.update_rows({'1': row})
        images.show(row)
        wait_for(root, lambda: tree.item('1', 'image') and images.frames)
    finally:
        images.close()
        root.destroy()


def test_default_and_minimum_layout_keep_import_controls_visible(tmp_path):
    root = tk.Tk()
    app = Application(root, tmp_path)
    app.tabs.select(app.import_tab)
    try:
        for geometry in ('1280x900', '1060x800'):
            root.geometry(geometry)
            app.action_notice.set('尚未开始导入：请打开小红书“已添加的表情”收藏面板，再点导入。任务状态保持原样。\n检查结果：等待页面元素「已添加的表情」超时。')
            root.update()
            widgets = []
            def collect(parent):
                for child in parent.winfo_children():
                    if isinstance(child, (ttk.Button, ttk.Checkbutton)):
                        widgets.append(child)
                    collect(child)
            collect(app.import_tab)
            for widget in widgets:
                label = widget.cget('text')
                assert widget.winfo_ismapped(), (geometry, label, 'hidden')
                assert widget.winfo_height() > 10, (geometry, label, 'collapsed')
                assert widget.winfo_rooty() + widget.winfo_height() <= root.winfo_rooty() + root.winfo_height(), (geometry, label, 'below window')
                assert widget.winfo_rootx() + widget.winfo_width() <= root.winfo_rootx() + root.winfo_width(), (geometry, label, 'beyond right edge')
    finally:
        app.close()
