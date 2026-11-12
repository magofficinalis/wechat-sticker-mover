import time
import tkinter as tk
from tkinter import ttk

from PIL import Image

from xhs_importer.gallery import GalleryView


def test_thousand_items_render_visible_tiles_and_mouse_selects_without_ctrl(tmp_path, monkeypatch):
    monkeypatch.setattr('xhs_importer.gallery.load_thumbnail',
                        lambda path, digest, size: Image.new('RGB', (size, size), 'blue'))
    root = tk.Tk()
    root.geometry('900x500')
    tree = ttk.Treeview(root)
    changes = []
    gallery = GalleryView(root, tree, lambda: changes.append(set(tree.selection())), {'pending': '待处理'})
    gallery.pack(fill='both', expand=True)
    rows = {}
    for i in range(1000):
        iid = str(i + 1)
        tree.insert('', 'end', iid=iid)
        rows[iid] = {'path': str(tmp_path / f'{i}.png'), 'digest': f'{i:064x}', 'status': 'pending'}
    try:
        gallery.update_rows(rows)
        gallery.set_active(True)
        root.update()
        width, columns, offset = gallery._dimensions()
        assert columns >= 2
        assert 0 < len(gallery.canvas.find_withtag('tile')) < 400
        assert len(gallery.jobs) <= gallery.MAX_JOBS
        gallery.canvas.event_generate('<Button-1>', x=offset + 30, y=30)
        gallery.canvas.event_generate('<Button-1>', x=offset + gallery.CELL_WIDTH + 30, y=30)
        root.update()
        assert set(tree.selection()) == {'1', '2'}
        gallery.canvas.event_generate('<Button-1>', x=offset + 30, y=30)
        root.update()
        assert set(tree.selection()) == {'2'}
        gallery.canvas.yview_moveto(1)
        root.update()
        assert gallery.canvas.yview()[0] > .9
        assert len(gallery.canvas.find_withtag('tile')) < 400
        assert changes
    finally:
        gallery.close()
        root.destroy()


def test_thumbnail_refresh_ignores_delayed_result_from_previous_generation(tmp_path, monkeypatch):
    import threading
    started, release = threading.Event(), threading.Event()
    calls = []
    def loader(path, digest, size):
        calls.append(path)
        if len(calls) == 1:
            started.set()
            release.wait(3)
            return None
        return Image.new('RGB', (size, size), 'red')
    monkeypatch.setattr('xhs_importer.gallery.load_thumbnail', loader)
    root = tk.Tk()
    root.geometry('400x300')
    tree = ttk.Treeview(root)
    tree.insert('', 'end', iid='1')
    gallery = GalleryView(root, tree, lambda: None, {'pending': '待处理'})
    gallery.pack(fill='both', expand=True)
    row = {'path': str(tmp_path / 'one.png'), 'digest': 'digest', 'status': 'pending'}
    try:
        gallery.update_rows({'1': row})
        gallery.set_active(True)
        root.update()
        assert started.wait(1)
        gallery.refresh()
        root.update()
        release.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            root.update()
            key = gallery._key(row)
            if key in gallery.cache and gallery.cache[key] is not None:
                break
            time.sleep(.01)
        assert gallery.cache.get(gallery._key(row)) is not None
    finally:
        release.set()
        gallery.close()
        root.destroy()
