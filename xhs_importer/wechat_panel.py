"""One-program WeChat export and Xiaohongshu import workflow."""
import json
import copy
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from .config import start_problem
from .device import Adb, AndroidDevice
from .engine import ImportEngine
from .store import Store
from .wechat_reader import discover_sources, check_stop
from .wechat_pipeline import run_wechat_pipeline, preflight_import


def choose_source(paths, saved):
    values = [str(p) for p in paths]
    if saved in values:
        return saved
    return values[0] if len(values) == 1 else ''


class WechatPanel:
    def __init__(self, app, parent, setup_parent=None):
        self.app = app
        self.path = app.data_dir / 'wechat-source.json'
        self.last_source_ids = []
        self.active = False
        self.discovery_id = None
        saved = ''
        try:
            if self.path.exists():
                saved = json.loads(self.path.read_text(encoding='utf-8')).get('database', '')
        except (OSError, ValueError, AttributeError):
            pass
        self.source = tk.StringVar(value=saved if isinstance(saved, str) else '')
        self.confirmed = app.confirmed
        self.summary = tk.StringVar()
        self.readiness = tk.StringVar()
        self.progress = tk.StringVar(value='首次完成设置后，即可把微信里的新增表情导入小红书。')
        self.primary_buttons = []
        source_parent = setup_parent if setup_parent is not None else parent
        ttk.Label(source_parent, text='选择微信来源与小红书账号', style='Section.TLabel').pack(anchor='w', pady=(0, 10))
        ttk.Label(source_parent, text='先登录电脑版微信，再选择自己的表情来源。', style='Muted.TLabel').pack(anchor='w')
        row = ttk.Frame(source_parent)
        row.pack(fill='x', pady=(6, 14))
        self.sources = ttk.Combobox(row, textvariable=self.source, state='readonly')
        self.sources.pack(side='left', fill='x', expand=True)
        app.controls.append(self.sources)
        app.button(row, '查找微信', self.discover).pack(side='left', padx=8)
        app.button(row, '手动选择', self.browse).pack(side='left')
        row = ttk.Frame(source_parent)
        row.pack(fill='x', pady=8)
        ttk.Label(row, text='小红书账号标签').pack(side='left', padx=(0, 10))
        app.entry(row, app.account, width=25).pack(side='left')
        ttk.Label(source_parent, text='账号名称用于区分导入记录，请与模拟器当前登录的小红书账号保持一致。', wraplength=760, style='Muted.TLabel').pack(anchor='w', pady=(8, 14))
        check = ttk.Checkbutton(source_parent, text='我确认这是本人的微信表情，且模拟器已登录上面的目标账号', variable=self.confirmed)
        check.pack(anchor='w', pady=6)
        app.controls.append(check)
        ttk.Label(source_parent, text='仅在本机读取表情记录、下载原图。不会读取聊天内容；读取密钥不保存、不显示。', wraplength=760, style='Muted.TLabel').pack(anchor='w', pady=(0, 12))
        ttk.Label(parent, text='把微信表情带到小红书', style='Section.TLabel').pack(anchor='w', pady=(0, 12))
        ttk.Label(parent, textvariable=self.summary, wraplength=840).pack(anchor='w', pady=(0, 12))
        self.home_confirmation = ttk.Checkbutton(parent,
            text='已确认上面的微信来源和小红书目标账号', variable=self.confirmed)
        self.home_confirmation.pack(anchor='w', pady=(0, 12))
        app.controls.append(self.home_confirmation)
        ttk.Label(parent, textvariable=self.readiness, wraplength=840, style='Muted.TLabel').pack(anchor='w', pady=(0, 16))
        ttk.Label(parent, text='开始前：登录电脑版微信，把模拟器停在小红书“已添加的表情”面板。', wraplength=840).pack(anchor='w', pady=(0, 16))
        actions = ttk.Frame(parent)
        actions.pack(fill='x', pady=10)
        for label, command, style in [('完成首次设置', self.primary_action, 'Accent.TButton'),
                                      ('先预览表情', self.preview, 'TButton'),
                                      ('从文件夹添加', lambda: app.add_local_images(), 'TButton')]:
            button = app.button(actions, label, command, style=style)
            button.pack(side='left', padx=(0, 10))
            self.primary_buttons.append(button)
        ttk.Separator(parent).pack(fill='x', pady=18)
        ttk.Label(parent, textvariable=self.progress, wraplength=900).pack(anchor='w', pady=(0, 12))
        ttk.Label(parent, text='重复表情会自动识别；需要你核对的图片会保留在“异常处理”。', wraplength=840, style='Muted.TLabel').pack(anchor='w', pady=16)
        self.source.trace_add('write', self.source_changed)
        self.confirmed.trace_add('write', lambda *_: self.refresh_home())
        self.refresh_home()

    def source_changed(self, *_):
        self.confirmed.set(False)
        self.refresh_home()

    def source_ready(self):
        source = Path(self.source.get())
        return source.name.lower() == 'emoticon.db' and source.is_file()

    def setup_needed(self):
        settings, profile = self.app.current_settings(), self.app.profile
        if not settings.adb or not settings.serial:
            return 0, '下一步：连接模拟器。'
        if not self.source_ready() or not settings.account:
            return 1, '下一步：选择微信来源，并确认当前小红书账号。'
        error = start_problem(settings, profile, True, True)
        if not error and profile.verifier:
            if not hasattr(self.app, 'store'):
                return 2, '正在读取已有的试导记录…'
            context = profile.verification_context(settings.account, settings.serial, settings.adb)
            verified = self.app.store.verified_formats(settings.account, context, require_visual=True)
            if not set(profile.tested_formats).issubset(verified):
                error = '当前环境还没有完整的单张试导记录。'
        if error:
            return 2, '下一步：完成首次试导与手机同步确认。'
        return None, '设置已就绪。开始时会检查模拟器和收藏页面。'

    def refresh_home(self):
        source = Path(self.source.get())
        if self.source_ready():
            name = source.parents[2].name if source.parent.name == 'emoticon' and source.parent.parent.name == 'db_storage' else source.parent.name
            source_label = name[:40] or '已选择'
        else:
            source_label = '未选择'
        step, message = self.setup_needed()
        formats = '、'.join(self.app.profile.tested_formats) if step is None else '完成试导后启用'
        self.summary.set(f'微信来源：{source_label}\n小红书账号：{self.app.account.get().strip() or "未填写"}    可自动导入：{formats}')
        if step is None and not self.confirmed.get():
            message = '设置已就绪，请先勾选确认当前微信来源与小红书账号。'
        self.readiness.set(message)
        self.primary_buttons[0].configure(text='导入微信新增表情' if step is None else '完成首次设置')

    def primary_action(self):
        if self.app.busy:
            return
        step, _ = self.setup_needed()
        if step is not None:
            self.app.show_setup(step)
        elif not self.confirmed.get():
            self.readiness.set('请先勾选确认当前微信来源与小红书账号，再开始导入。')
        else:
            self.start(True)

    def preview(self):
        if self.app.busy:
            return
        if not self.source_ready() or not self.app.account.get().strip():
            self.app.show_setup(1)
        elif not self.confirmed.get():
            self.readiness.set('请先勾选确认当前微信来源与小红书账号，再读取表情。')
        else:
            self.start(False)

    def invalidate(self):
        self.confirmed.set(False)

    def save(self):
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'database': self.source.get()}, ensure_ascii=False), encoding='utf-8')
        temporary.replace(self.path)

    def discover(self):
        self.discovery_id = None
        if self.app.busy:
            return
        saved = self.source.get()
        def done(paths):
            self.sources.configure(values=[str(p) for p in paths])
            self.source.set(choose_source(paths, saved))
            if not paths:
                self.progress.set('没有找到默认微信目录。点击“手动选择”，定位 xwechat_files 下的账号/db_storage/emoticon/emoticon.db。')
            elif len(paths) > 1 and not self.source.get():
                self.progress.set('找到多个微信来源，请从下拉列表选择当前登录的账号目录。')
            else:
                self.progress.set('已找到微信来源。确认目标账号后，可以开始；首次也可先只读取并预览。')
        self.app.background(lambda: discover_sources(saved), done)

    def browse(self):
        path = filedialog.askopenfilename(parent=self.app.root, title='选择微信表情数据库 emoticon.db', filetypes=[('微信表情数据库', 'emoticon.db')])
        if path:
            self.sources.configure(values=[path])
            self.source.set(path)
            self.save()

    def start(self, import_after):
        app = self.app
        if app.busy:
            return
        if app.calibration_window is not None:
            messagebox.showinfo('请先完成适配', '关闭界面适配窗口后再开始。', parent=app.root)
            return
        source = Path(self.source.get())
        settings = app.current_settings()
        if not source.is_file() or source.name.lower() != 'emoticon.db':
            messagebox.showinfo('选择微信来源', '请先选择当前微信账号的 emoticon.db。', parent=app.root)
            return
        if not settings.account or not self.confirmed.get():
            messagebox.showinfo('确认账号', '填写小红书账号标签，并勾选来源与目标账号确认。', parent=app.root)
            return
        # Freeze every target/setting before dispatch; Tk variables stay on the UI thread.
        profile = copy.deepcopy(app.profile)
        if import_after:
            error = start_problem(settings, profile, True, True)
            if error:
                self.progress.set(error + ' 可先使用“只读取并预览”。')
                return
        skip_errors = app.skip_errors.get()
        formats = profile.tested_formats if (profile.tested and profile.tested_context ==
            profile.verification_context(settings.account, settings.serial, settings.adb)) else []
        self.save()
        app.stop_event.clear()
        self.active = True
        self.progress.set('正在准备微信读取…')
        def notify(message):
            app.events.put(('wechat-progress', message))
        def task():
            device = None
            if import_after:
                device = AndroidDevice(Adb(settings.adb), settings.serial)
                store = Store(app.db_path)
                try:
                    preflight_import(settings, profile, store, device, app.stop_event)
                finally:
                    store.close()
            def import_batch(ids):
                check_stop(app.stop_event)
                store = Store(app.db_path)
                try:
                    engine = ImportEngine(store, device, profile, settings.account,
                        lambda kind, msg: app.events.put((kind, msg)), app.stop_event, settings.adb)
                    engine.run(None, True, item_ids=ids, skip_errors=skip_errors)
                finally:
                    store.close()
            return run_wechat_pipeline(source, app.data_dir, settings.account, formats,
                app.stop_event, notify, import_fn=import_batch if import_after else None)
        def done(result):
            exported, batch = result
            self.last_source_ids = batch.source_ids
            report = {'exported': exported.exported, 'cached': exported.cached, 'failed': exported.failed,
                      'new_tasks': batch.added, 'existing_tasks': batch.duplicate, 'invalid': batch.invalid,
                      'unverified_format_tasks': len(batch.unverified_ids), 'issues': exported.issues}
            (app.data_dir / 'wechat-last-result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            rows = [r for r in app.store.rows(settings.account) if r['id'] in set(batch.source_ids)]
            counts = {state: sum(r['status'] == state for r in rows) for state in ('success','pending','skipped','uncertain','failed')}
            summary = (f'新导出 {exported.exported} 张，复用缓存 {exported.cached} 张，未取得原图 {exported.failed} 张。\n'
                f'新建任务 {batch.added} 张，已有记录 {batch.duplicate} 张；本批累计已收藏 {counts["success"]} 张、待处理 {counts["pending"]} 张、跳过 {counts["skipped"]} 张、待核对 {counts["uncertain"]} 张、失败 {counts["failed"]} 张。')
            if batch.unverified_ids:
                summary += f'\n其中 {len(batch.unverified_ids)} 张格式尚未验证，可到“设置与帮助”完成试导。'
            if exported.failed or batch.invalid:
                summary += '\n读取明细保存在数据目录 wechat-last-result.json。'
            self.progress.set(summary)
            app.status.set(summary.replace('\n', ' '))
            app.task_images.refresh()
            app.gallery.refresh()
            app.reload_rows()
            if not import_after and app.account.get().strip() == settings.account:
                self.view_tasks()
        app.background(task, done)

    def view_tasks(self):
        app = self.app
        app.tabs.select(app.import_tab)
        app.reload_rows()
        rows = [r for r in app.rows_cache.values() if r['id'] in set(self.last_source_ids)]
        row = next((r for r in rows if r['status'] == 'pending'), rows[0] if rows else None)
        if row:
            iid = str(row['id'])
            app.tree.selection_set(iid)
            app.tree.focus(iid)
            app.tree.see(iid)
            app.row_detail()

    def close(self):
        if self.discovery_id is not None:
            self.app.root.after_cancel(self.discovery_id)
        self.save()
