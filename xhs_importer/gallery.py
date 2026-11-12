"""Virtualized thumbnail grid using the task list's selection as its source of truth."""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import queue
import tkinter as tk
from tkinter import ttk

from PIL import ImageTk

from .preview import load_thumbnail


class GalleryView(ttk.Frame):
    CELL_WIDTH = 150
    CELL_HEIGHT = 170
    IMAGE_SIZE = 112
    MAX_JOBS = 48

    def __init__(self, master, tree, on_select, labels):
        super().__init__(master)
        self.tree, self.on_select, self.labels = tree, on_select, labels
        self.rows, self.ids = {}, []
        self.active, self.closed = False, False
        self.cache, self.jobs = OrderedDict(), {}
        self.results = queue.Queue()
        self.workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix='gallery-thumb')
        self.draw_id, self.wheel_remainder = None, 0
        self.generation, self.region = 0, None
        ttk.Label(self, text='点击图片选中或取消，可连续选择多张。', style='Muted.TLabel').grid(
            row=0, column=0, columnspan=2, sticky='w', pady=(0, 5))
        self.canvas = tk.Canvas(self, background='white', highlightthickness=0, takefocus=1)
        self.scroll = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._scroll_changed)
        self.canvas.grid(row=1, column=0, sticky='nsew')
        self.scroll.grid(row=1, column=1, sticky='ns')
        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)
        self.canvas.bind('<Configure>', lambda _: self.request_draw())
        self.canvas.bind('<Button-1>', self._click)
        self.canvas.bind('<MouseWheel>', self._wheel)
        self.canvas.bind('<Button-4>', self._wheel)
        self.canvas.bind('<Button-5>', self._wheel)
        self.canvas.bind('<space>', self._space)
        for key, delta in [('Left', -1), ('Right', 1), ('Up', -1), ('Down', 1)]:
            self.canvas.bind(f'<{key}>', lambda event, k=key, d=delta: self._move(k, d))
        self.poll_id = self.after(80, self._poll)

    def _key(self, row):
        return self.generation, row['digest'], row['path']

    def refresh(self):
        self.generation += 1
        self.cache.clear()
        for future in self.jobs.values():
            future.cancel()
        self.jobs.clear()
        self.request_draw()

    def update_rows(self, rows):
        self.rows = dict(rows)
        self.ids = list(rows)
        valid = {self._key(row) for row in rows.values()}
        for key in list(self.cache):
            if key not in valid:
                del self.cache[key]
        for key, future in list(self.jobs.items()):
            if key not in valid and future.cancel():
                del self.jobs[key]
        self.request_draw()

    def set_active(self, active):
        self.active = active
        if active:
            self.request_draw()

    def selected_ids(self):
        return set(self.tree.selection())

    def toggle_item(self, iid):
        if iid not in self.rows or not self.tree.exists(iid):
            return
        if iid in self.selected_ids():
            self.tree.selection_remove(iid)
            if self.tree.focus() == iid:
                remaining = self.tree.selection()
                self.tree.focus(remaining[-1] if remaining else '')
        else:
            self.tree.selection_add(iid)
            self.tree.focus(iid)
            self.tree.see(iid)
        self.on_select()
        self.request_draw()

    def _dimensions(self):
        width = max(1, self.canvas.winfo_width())
        columns = max(1, width // self.CELL_WIDTH)
        offset = max(0, (width - columns * self.CELL_WIDTH) // 2)
        return width, columns, offset

    def _click(self, event):
        _, columns, offset = self._dimensions()
        x, y = self.canvas.canvasx(event.x) - offset, self.canvas.canvasy(event.y)
        if x < 0 or y < 0:
            return
        column = int(x // self.CELL_WIDTH)
        index = int(y // self.CELL_HEIGHT) * columns + column
        if column < columns and index < len(self.ids):
            self.canvas.focus_set()
            self.toggle_item(self.ids[index])

    def _space(self, _event):
        iid = self.tree.focus()
        if iid in self.rows:
            self.toggle_item(iid)
        return 'break'

    def _move(self, key, direction):
        if not self.ids:
            return 'break'
        _, columns, _ = self._dimensions()
        current = self.tree.focus()
        index = self.ids.index(current) if current in self.rows else 0
        index = max(0, min(len(self.ids) - 1, index + direction * (columns if key in {'Up', 'Down'} else 1)))
        self.tree.focus(self.ids[index])
        self._see_index(index, columns)
        self.request_draw()
        return 'break'

    def _see_index(self, index, columns):
        top = (index // columns) * self.CELL_HEIGHT
        height = max(1, self.canvas.winfo_height())
        total = max(height, ((len(self.ids) + columns - 1) // columns) * self.CELL_HEIGHT)
        current = self.canvas.canvasy(0)
        if top < current:
            self.canvas.yview_moveto(top / total)
        elif top + self.CELL_HEIGHT > current + height:
            self.canvas.yview_moveto((top + self.CELL_HEIGHT - height) / total)

    def _wheel(self, event):
        if getattr(event, 'num', None) in (4, 5):
            units = -3 if event.num == 4 else 3
        elif self.tk.call('tk', 'windowingsystem') == 'aqua':
            units = -int(event.delta) * 3
        else:
            self.wheel_remainder += event.delta
            notches = int(self.wheel_remainder / 120)
            self.wheel_remainder -= notches * 120
            units = -notches * 3
        if units:
            self.canvas.yview_scroll(units, 'units')
        return 'break'

    def _scroll_changed(self, first, last):
        self.scroll.set(first, last)
        self.request_draw()

    def request_draw(self):
        if self.active and not self.closed and self.draw_id is None:
            self.draw_id = self.after_idle(self._draw)

    def _submit(self, key):
        results = self.results
        future = self.workers.submit(load_thumbnail, key[2], key[1], self.IMAGE_SIZE)
        self.jobs[key] = future
        def done(job):
            if job.cancelled():
                return
            try:
                results.put((key, job.result()))
            except Exception:
                results.put((key, None))
        future.add_done_callback(done)

    def _draw(self):
        self.draw_id = None
        if not self.active or self.closed:
            return
        width, columns, offset = self._dimensions()
        count_rows = (len(self.ids) + columns - 1) // columns
        region = (0, 0, width, max(1, count_rows * self.CELL_HEIGHT))
        if region != self.region:
            self.region = region
            self.canvas.configure(scrollregion=region)
        top = max(0, int(self.canvas.canvasy(0) // self.CELL_HEIGHT) - 1)
        bottom = min(count_rows, int(self.canvas.canvasy(self.canvas.winfo_height()) // self.CELL_HEIGHT) + 2)
        visible = self.ids[top * columns:bottom * columns]
        visible_keys = {self._key(self.rows[iid]) for iid in visible}
        for key, future in list(self.jobs.items()):
            if key not in visible_keys and future.cancel():
                del self.jobs[key]
        self.canvas.delete('tile')
        selected, focused = self.selected_ids(), self.tree.focus()
        for index in range(top * columns, min(len(self.ids), bottom * columns)):
            iid, row = self.ids[index], self.rows[self.ids[index]]
            x = offset + (index % columns) * self.CELL_WIDTH
            y = (index // columns) * self.CELL_HEIGHT
            selected_tile = iid in selected
            self.canvas.create_rectangle(x + 3, y + 3, x + self.CELL_WIDTH - 3, y + self.CELL_HEIGHT - 3,
                fill='#e8f2ff' if selected_tile else '#ffffff',
                outline='#3077c5' if selected_tile else ('#9ba8b7' if focused == iid else '#e3e7ec'),
                width=2 if selected_tile else 1, tags='tile')
            key = self._key(row)
            if key in self.cache:
                photo = self.cache[key]
                self.cache.move_to_end(key)
                if photo:
                    self.canvas.create_image(x + self.CELL_WIDTH // 2, y + 10, image=photo, anchor='n', tags='tile')
                else:
                    self.canvas.create_text(x + self.CELL_WIDTH // 2, y + 66, text='无法预览', fill='#8a94a3', tags='tile')
            else:
                self.canvas.create_text(x + self.CELL_WIDTH // 2, y + 66, text='加载中…', fill='#8a94a3', tags='tile')
                if key not in self.jobs and len(self.jobs) < self.MAX_JOBS:
                    self._submit(key)
            name = Path(row['path']).name
            label = name if len(name) <= 17 else name[:13] + '…' + Path(name).suffix
            self.canvas.create_text(x + self.CELL_WIDTH // 2, y + 128, text=label, fill='#263445', tags='tile')
            status = self.labels.get(row['status'], row['status'])
            self.canvas.create_text(x + self.CELL_WIDTH // 2, y + 151,
                text=('✓ 已选 · ' if selected_tile else '') + status,
                fill='#24649b' if selected_tile else '#657184', tags='tile')
        limit = max(128, len(visible_keys) + columns * 2)
        while len(self.cache) > limit:
            key = next(iter(self.cache))
            if key in visible_keys:
                self.cache.move_to_end(key)
                continue
            self.cache.popitem(last=False)

    def _poll(self):
        changed = False
        for _ in range(24):
            try:
                key, value = self.results.get_nowait()
            except queue.Empty:
                break
            self.jobs.pop(key, None)
            if key in {self._key(row) for row in self.rows.values()}:
                self.cache[key] = ImageTk.PhotoImage(value, master=self) if value is not None else None
                changed = True
        if changed:
            self.request_draw()
        if not self.closed:
            self.poll_id = self.after(80, self._poll)

    def close(self):
        self.closed = True
        if self.draw_id is not None:
            self.after_cancel(self.draw_id)
        self.after_cancel(self.poll_id)
        self.workers.shutdown(wait=False, cancel_futures=True)
