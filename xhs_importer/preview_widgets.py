"""Task list thumbnails and a single animated preview, owned by the Tk thread."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import queue
import tkinter as tk
from tkinter import ttk

from PIL import ImageTk

from .preview import load_preview, load_thumbnail


class TaskImages:
    def __init__(self, root, tree, parent):
        self.root, self.tree = root, tree
        self.results = queue.Queue()
        self.thumbs = ThreadPoolExecutor(max_workers=1, thread_name_prefix='thumbnail')
        self.previews = ThreadPoolExecutor(max_workers=1, thread_name_prefix='preview')
        self.cache, self.jobs, self.rows = {}, {}, {}
        self.token = 0
        self.generation = 0
        self.current = None
        self.preview_job = None
        self.frames, self.durations = [], []
        self.frame_index = 0
        self.animation_id = None
        self.closed = False
        ttk.Label(parent, text='选中图片预览', style='Section.TLabel').pack(anchor='w', pady=(0, 10))
        box = ttk.Frame(parent, width=204, height=204)
        box.pack(fill='x')
        box.pack_propagate(False)
        self.picture = ttk.Label(box, anchor='center', text='点击列表中的图片')
        self.picture.pack(fill='both', expand=True)
        self.name = tk.StringVar()
        self.note = tk.StringVar(value='列表显示首帧，动图在这里播放。')
        ttk.Label(parent, textvariable=self.name, wraplength=204).pack(anchor='w', pady=(12, 6))
        ttk.Label(parent, textvariable=self.note, wraplength=204, style='Muted.TLabel').pack(anchor='w')
        self.poll_id = root.after(80, self.poll)

    def key(self, row):
        return self.generation, row['digest'], row['path']

    def refresh(self):
        """A new scan may restore/replace a source; do not reuse stale results."""
        self.generation += 1
        self.token += 1
        self.current = None
        self.stop_animation()
        if self.preview_job:
            self.preview_job.cancel()
        for job in self.jobs.values():
            job.cancel()
        self.jobs.clear()
        self.cache.clear()
        self.picture.configure(image='', text='点击列表中的图片')
        self.frames = []

    def submit(self, executor, kind, key, function, *args):
        future = executor.submit(function, *args)
        results = self.results
        def complete(job):
            if job.cancelled():
                return
            try:
                value, error = job.result(), ''
            except Exception as exc:
                value, error = None, str(exc)
            # Workers must not own the Tk controller: its finalizer belongs to the UI thread.
            results.put((kind, key, value, error))
        future.add_done_callback(complete)
        return future

    def update_rows(self, rows):
        self.rows = rows
        keys = {self.key(row) for row in rows.values()}
        self.cache = {k: v for k, v in self.cache.items() if k in keys}
        for key, job in list(self.jobs.items()):
            if key not in keys:
                job.cancel()
                del self.jobs[key]
        for iid, row in rows.items():
            key = self.key(row)
            if key in self.cache:
                image = self.cache[key]
                self.tree.item(iid, image=image or '', text='' if image else '无预览')
            elif key not in self.jobs:
                self.jobs[key] = self.submit(self.thumbs, 'thumb', key, load_thumbnail, row['path'], row['digest'])

    def show(self, row):
        key = self.key(row) if row else None
        if key == self.current:
            return
        self.current = key
        self.token += 1
        self.stop_animation()
        if self.preview_job:
            self.preview_job.cancel()
        self.picture.configure(image='', text='加载预览…' if row else '点击列表中的图片')
        self.frames = []
        self.name.set(Path(row['path']).name if row else '')
        self.note.set('' if row else '列表显示首帧，动图在这里播放。')
        if row:
            self.preview_job = self.submit(self.previews, 'preview', self.token, load_preview, row['path'], row['digest'])

    def stop_animation(self):
        if self.animation_id is not None:
            self.root.after_cancel(self.animation_id)
            self.animation_id = None

    def animate(self):
        self.animation_id = None
        if self.closed or not self.frames:
            return
        index = self.frame_index
        self.picture.configure(image=self.frames[index], text='')
        self.frame_index = (index + 1) % len(self.frames)
        if len(self.frames) > 1:
            self.animation_id = self.root.after(self.durations[index], self.animate)

    def poll(self):
        for _ in range(24):
            try:
                kind, key, value, error = self.results.get_nowait()
            except queue.Empty:
                break
            if kind == 'thumb':
                self.jobs.pop(key, None)
                matches = [iid for iid, row in self.rows.items() if self.key(row) == key]
                if matches:
                    photo = ImageTk.PhotoImage(value, master=self.root) if value else None
                    self.cache[key] = photo
                    for iid in matches:
                        self.tree.item(iid, image=photo or '', text='' if photo else '无预览')
            elif key == self.token:
                if error:
                    self.picture.configure(image='', text='无法显示预览')
                    self.note.set('原文件不可读取或已变化，请检查路径并重新扫描。\n' + error)
                else:
                    self.frames = [ImageTk.PhotoImage(im, master=self.root) for im in value.frames]
                    self.durations = value.durations
                    self.note.set(value.note)
                    self.frame_index = 0
                    self.animate()
        self.poll_id = self.root.after(80, self.poll)

    def close(self):
        self.closed = True
        self.token += 1
        self.stop_animation()
        self.root.after_cancel(self.poll_id)
        self.thumbs.shutdown(wait=False, cancel_futures=True)
        self.previews.shutdown(wait=False, cancel_futures=True)
