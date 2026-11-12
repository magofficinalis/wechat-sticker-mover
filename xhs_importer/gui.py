import csv
import copy
from dataclasses import asdict
import json
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText
import webbrowser

from .catalog import scan_folder
from .config import Settings, default_adb, start_problem
from .device import Adb, AndroidDevice
from .engine import ImportEngine
from .profile import Profile
from .preview_widgets import TaskImages
from .store import Store
from .wechat_panel import WechatPanel
from .exception_panel import ExceptionPanel
from .help_view import GuideView
from .setup_panel import SetupPanel, HELP_TEXT
from .widgets import OverflowScrollbar
from .batch_progress import BatchProgress
from .gallery import GalleryView

LABELS = {'pending': '待处理', 'running': '处理中', 'submitting': '添加中', 'uncertain': '待核对',
          'success': '已收藏', 'failed': '失败', 'skipped': '跳过'}


class Application:
    def __init__(self, root, data_dir):
        self.root, self.data_dir = root, Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.data_dir / 'settings.json'
        self.db_path = self.data_dir / 'queue.sqlite3'
        self.events = queue.Queue()
        self.busy = False
        self.stop_event = threading.Event()
        self.calibration_window = None
        self.worker = None
        self.initial_error = ''
        try:
            settings = Settings.load(self.settings_path)
        except ValueError as exc:
            settings = Settings()
            self.initial_error = str(exc)
        try:
            path = self.data_dir / 'profile.json'
            self.profile = Profile.load(path) if path.exists() else Profile()
        except (ValueError, TypeError, OSError) as exc:
            self.profile = Profile()
            self.initial_error += f' 界面配置未载入：{exc}'
        self.adb = tk.StringVar(value=settings.adb or default_adb())
        self.serial = tk.StringVar(value=settings.serial)
        self.account = tk.StringVar(value=settings.account)
        self.folder = tk.StringVar(value=settings.folder)
        self.endpoint = tk.StringVar()
        self.confirmed = tk.BooleanVar(value=False)
        self.auto = tk.BooleanVar(value=False)
        self.skip_errors = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value='准备就绪 · 先选择图片文件夹，或查看安装指引。')
        self.action_notice = tk.StringVar(value='开始前请打开小红书“已添加的表情”收藏面板。')
        self.selection_notice = tk.StringVar(value='一键选择剩余待处理图片，也可 Ctrl / Shift 多选。')
        self.counts = tk.StringVar()
        self.profile_status = tk.StringVar()
        self.rows_cache = {}
        self.batch_progress = None
        self.batch_text = tk.StringVar(value='')
        self.last_duplicates = []
        self.trial_item_id, self.trial_context = None, ''
        self.trial_status = tk.StringVar(value='选择一张还没有收藏的图片，完成首次验证。')
        self.controls = []
        self.build()
        for variable in (self.account, self.serial, self.adb):
            variable.trace_add('write', self.context_changed)
        self.store = Store(self.db_path)
        for account in [r[0] for r in self.store.db.execute('SELECT DISTINCT account FROM items')]:
            self.store.recover(account)
        self.reload_rows()
        self.refresh_profile()
        self.poll_id = self.root.after(120, self.poll)
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        if self.initial_error:
            self.root.after(250, lambda: messagebox.showwarning('配置需要检查', self.initial_error, parent=root))

    def button(self, parent, text, command, **kwargs):
        widget = ttk.Button(parent, text=text, command=command, **kwargs)
        self.controls.append(widget)
        return widget

    def entry(self, parent, variable, **kwargs):
        widget = ttk.Entry(parent, textvariable=variable, **kwargs)
        self.controls.append(widget)
        return widget

    def build(self):
        r = self.root
        r.title('表情搬运助手 · 小红书导入')
        r.geometry('1280x900')
        r.minsize(1060, 800)
        r.configure(bg='#f4f5f7')
        style = ttk.Style(r)
        style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei UI', 10), background='#f4f5f7')
        style.configure('TButton', padding=(12, 7))
        style.configure('TEntry', padding=6, fieldbackground='white')
        style.configure('TNotebook.Tab', padding=(18, 9))
        style.configure('Treeview', rowheight=31, background='white', fieldbackground='white', borderwidth=0)
        style.configure('Sticker.Treeview', rowheight=74)
        style.configure('Treeview.Heading', font=('Microsoft YaHei UI', 10, 'bold'), padding=7)
        style.configure('Hero.TLabel', font=('Microsoft YaHei UI', 23, 'bold'), foreground='#1c2839')
        style.configure('Section.TLabel', font=('Microsoft YaHei UI', 13, 'bold'), foreground='#243247')
        style.configure('Muted.TLabel', foreground='#657184')
        style.configure('Accent.TButton', foreground='white', background='#dc425c', font=('Microsoft YaHei UI', 10, 'bold'))
        style.map('Accent.TButton', background=[('active', '#bc304a'), ('disabled', '#c4c7cd')])
        style.configure('Batch.Horizontal.TProgressbar', background='#dc425c', troughcolor='#dfe5ed',
                        bordercolor='#dfe5ed', lightcolor='#dc425c', darkcolor='#dc425c')
        outer = ttk.Frame(r, padding=(24, 18))
        outer.pack(fill='both', expand=True)
        header = ttk.Frame(outer)
        header.pack(fill='x', pady=(0, 14))
        ttk.Label(header, text='表情搬运助手', style='Hero.TLabel').pack(side='left')
        self.pause_button = ttk.Button(header, text='暂停任务', command=self.stop_task, state='disabled')
        self.pause_button.pack(side='right', pady=12)
        ttk.Label(outer, text='微信收藏 → 小红书收藏 · 原图搬运，自动去重', style='Muted.TLabel').pack(anchor='w', pady=(0, 12))
        self.batch_frame = ttk.Frame(outer)
        ttk.Label(self.batch_frame, textvariable=self.batch_text, wraplength=1040).pack(anchor='w')
        self.batch_bar = ttk.Progressbar(self.batch_frame, orient='horizontal', mode='determinate',
                                         maximum=100, style='Batch.Horizontal.TProgressbar')
        self.batch_bar.pack(fill='x', pady=(4, 0))
        self.tabs = ttk.Notebook(outer)
        self.tabs.pack(fill='both', expand=True)
        self.wechat_tab, self.import_tab, self.exception_tab, self.settings_tab = [ttk.Frame(self.tabs, padding=18) for _ in range(4)]
        for tab, label in [(self.wechat_tab,'开始搬运'),(self.import_tab,'我的表情'),(self.exception_tab,'异常处理'),(self.settings_tab,'设置与帮助')]:
            self.tabs.add(tab, text=label)
        self.setup_panel = SetupPanel(self,self.settings_tab)
        self.connect_tab = self.setup_panel.connection_page
        self.profile_tab = self.setup_panel.verification_page
        self.help_tab = self.setup_panel.help_page
        self.build_import()
        self.build_connection()
        self.build_profile()
        self.build_help()
        self.exception_panel = ExceptionPanel(self, self.exception_tab)
        self.wechat_panel = WechatPanel(self, self.wechat_tab, setup_parent=self.setup_panel.source_page)
        self.tabs.select(self.wechat_tab)
        ttk.Separator(outer).pack(fill='x', pady=(14, 8))
        ttk.Label(outer, textvariable=self.status, wraplength=1060, style='Muted.TLabel').pack(anchor='w')

    def build_import(self):
        f = self.import_tab
        toolbar = ttk.Frame(f)
        toolbar.pack(fill='x',pady=(0,8))
        ttk.Label(toolbar,textvariable=self.selection_notice,wraplength=620,style='Muted.TLabel').pack(side='left')
        more=ttk.Menubutton(toolbar,text='更多 ▾')
        more.pack(side='right')
        self.grid_view_button = self.button(toolbar, '缩略图', lambda: self.show_import_view('grid'))
        self.grid_view_button.pack(side='right', padx=(4, 8))
        self.list_view_button = self.button(toolbar, '列表', lambda: self.show_import_view('list'), style='Accent.TButton')
        self.list_view_button.pack(side='right')
        self.controls.append(more)
        menu=tk.Menu(more,tearoff=False)
        menu.add_command(label='继续普通待处理队列',command=lambda:self.start_product_import(False))
        menu.add_command(label='暂不导入选中图片',command=self.skip_selected)
        menu.add_separator()
        menu.add_command(label='重新扫描上次文件夹',command=self.scan)
        menu.add_command(label='查看重复明细',command=self.show_duplicates)
        menu.add_command(label='导出结果 CSV',command=self.export_csv)
        more.configure(menu=menu)
        ttk.Label(f, textvariable=self.counts, style='Section.TLabel').pack(anchor='w', pady=(3, 10))
        footer = ttk.Frame(f)
        footer.pack(side='bottom', fill='x')
        content = ttk.Frame(f)
        content.pack(fill='both', expand=True)
        sidebar = ttk.Frame(content, width=216, padding=(12, 0, 0, 0))
        sidebar.pack(side='right', fill='y')
        sidebar.pack_propagate(False)
        table = ttk.Frame(content)
        table.pack(side='left', fill='both', expand=True)
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        self.list_view = ttk.Frame(table)
        self.list_view.grid(row=0, column=0, sticky='nsew')
        self.list_view.rowconfigure(0, weight=1)
        self.list_view.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(self.list_view, columns=('name', 'type', 'status'),
                                show='tree headings', selectmode='extended', style='Sticker.Treeview', height=3)
        self.tree.heading('#0', text='预览')
        self.tree.column('#0', width=86, minwidth=86, stretch=False)
        for key, label, width in [('name', '文件', 240), ('type', '格式 / 帧数', 120), ('status', '状态', 100)]:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=70, stretch=key in {'name', 'note'})
        scroll = ttk.Scrollbar(self.list_view, orient='vertical', command=self.tree.yview)
        horizontal = OverflowScrollbar(self.list_view, orient='horizontal', command=self.tree.xview)
        self.tree.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        self.tree.grid(row=0, column=0, sticky='nsew')
        scroll.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        self.gallery = GalleryView(table, self.tree, self.row_detail, LABELS)
        self.gallery.grid(row=0, column=0, sticky='nsew')
        self.gallery.grid_remove()
        self.task_images = TaskImages(self.root, self.tree, sidebar)
        self.tree.tag_configure('success', foreground='#23805a')
        self.tree.tag_configure('uncertain', foreground='#a66c00')
        self.tree.tag_configure('failed', foreground='#b52d45')
        self.tree.bind('<<TreeviewSelect>>', self.selection_changed)
        self.detail = tk.StringVar(value='选中一张图片可查看说明；失败和待核对任务需要人工确认。')
        self.detail_area=ScrolledText(footer,height=3,wrap='word',font=('Microsoft YaHei UI',10),state='disabled')
        self.detail_area.pack(fill='x',pady=(8,4))
        def display_detail(*_):
            self.detail_area.configure(state='normal')
            self.detail_area.delete('1.0','end')
            self.detail_area.insert('1.0',self.detail.get())
            self.detail_area.configure(state='disabled')
        self.detail.trace_add('write',display_detail)
        display_detail()
        ttk.Label(footer,textvariable=self.action_notice,wraplength=950).pack(anchor='w',pady=(0,6))
        actions=ttk.Frame(footer)
        actions.pack(fill='x')
        self.button(actions,'全选待导入',self.select_all_pending).pack(side='left',padx=(0,8))
        self.button(actions,'导入选中',self.start_product_import,style='Accent.TButton').pack(side='left')

    def show_import_view(self, view):
        if view == 'grid':
            self.list_view.grid_remove()
            self.gallery.grid()
            self.gallery.set_active(True)
            self.grid_view_button.configure(style='Accent.TButton')
            self.list_view_button.configure(style='TButton')
        elif view == 'list':
            self.gallery.set_active(False)
            self.gallery.grid_remove()
            self.list_view.grid()
            self.list_view_button.configure(style='Accent.TButton')
            self.grid_view_button.configure(style='TButton')
        else:
            raise ValueError('未知图片视图')

    def selection_changed(self, _event=None):
        self.row_detail()
        self.gallery.request_draw()

    def build_connection(self):
        f=self.connect_tab
        ttk.Label(f,text='先启动模拟器，再选择正在使用的设备',style='Section.TLabel').pack(anchor='w',pady=(0,12))
        ttk.Label(f,text='雷电设置 → 其他设置 → ADB 调试 → 本地连接。保存并重启后，在模拟器登录小红书。',wraplength=890).pack(anchor='w',pady=(0,18))
        row=ttk.Frame(f)
        row.pack(fill='x')
        self.devices=ttk.Combobox(row,textvariable=self.serial,state='readonly',width=35)
        self.devices.pack(side='left')
        self.controls.append(self.devices)
        self.button(row,'查找模拟器',self.discover,style='Accent.TButton').pack(side='left',padx=10)
        self.device_note=tk.StringVar(value='选择目标设备后，点击上方“2 微信和账号”。找不到设备可查看高级选项。')
        ttk.Label(f,textvariable=self.device_note,wraplength=890).pack(anchor='w',pady=18)
        self.button(f,'下载安装雷电',lambda:webbrowser.open('https://www.ldplayer.net/versions')).pack(anchor='w')

    def build_profile(self):
        f=self.profile_tab
        ttk.Label(f,text='一次试导，确认后开始搬运',style='Section.TLabel').pack(anchor='w')
        ttk.Label(f,textvariable=self.profile_status,wraplength=890).pack(anchor='w',pady=10)
        ttk.Label(f,text='把模拟器停在聊天里的“已添加的表情”面板，再点击自动试导。程序会自动检测并准备支持的配置。',wraplength=890).pack(anchor='w',pady=(0,10))
        from .trial_panel import TrialPanel
        self.trial_panel=TrialPanel(self,f)
        ttk.Separator(f).pack(fill='x',pady=4)
        manual=ttk.Frame(f)
        def toggle_manual():
            if manual.winfo_manager():
                manual.pack_forget()
            else:
                manual.pack(fill='x')
        self.button(f,'手动单张试导（备用）',toggle_manual).pack(anchor='w',pady=6)
        f=manual
        self.button(f,'检测并准备小红书',self.prepare_profile).pack(anchor='w',pady=(6,10))
        ttk.Label(f,text='选择未收藏的图片试导，再去手机核对同步；动图还需检查动画。每种格式验证一次。',wraplength=890).pack(anchor='w',pady=10)
        self.button(f,'选择图片并试导 1 张',self.trial_selected_file,style='Accent.TButton').pack(anchor='w',pady=5)
        ttk.Label(f,textvariable=self.trial_status,wraplength=890).pack(anchor='w',pady=10)
        self.trial_confirm_button=self.button(f,'手机已同步，确认成功',self.confirm_trial_sync)
        self.trial_confirm_button.pack(anchor='w',pady=(0,8))
        self.button(f,'没有成功？去异常处理',lambda:self.tabs.select(self.exception_tab)).pack(anchor='w')
        self.test_formats={fmt:tk.BooleanVar(value=fmt in self.profile.tested_formats) for fmt in ('PNG','JPEG','GIF','WEBP')}
        self.sync_checked=tk.BooleanVar(value=self.profile.phone_sync)
        advanced=self.setup_panel.advanced_page
        ttk.Label(advanced,text='仅在连接失败或需要自定义适配时使用。',style='Muted.TLabel').pack(anchor='w',pady=(0,8))
        row=ttk.Frame(advanced);row.pack(fill='x',pady=5)
        ttk.Label(row,text='ADB 程序',width=12).pack(side='left')
        self.entry(row,self.adb).pack(side='left',fill='x',expand=True)
        self.button(row,'选择',self.choose_adb).pack(side='left',padx=8)
        row=ttk.Frame(advanced);row.pack(fill='x',pady=5)
        ttk.Label(row,text='本机地址',width=12).pack(side='left')
        self.entry(row,self.endpoint,width=28).pack(side='left')
        self.button(row,'连接端口',self.connect_endpoint).pack(side='left',padx=8)
        ttk.Label(advanced,text='填写模拟器实际的 127.0.0.1:端口；连接后回到第 1 步查找设备。',wraplength=890,style='Muted.TLabel').pack(anchor='w',pady=5)
        row=ttk.Frame(advanced);row.pack(fill='x',pady=14)
        self.button(row,'界面适配说明 / 手动记录',self.calibrate).pack(side='left')
        self.button(row,'导入配置',self.import_profile).pack(side='left',padx=8)
        self.button(row,'导出配置',self.export_profile).pack(side='left')
        check=ttk.Checkbutton(advanced,text='自动模式遇到异常时先跳过，确认返回收藏页后继续',variable=self.skip_errors)
        check.pack(anchor='w',pady=8);self.controls.append(check)
        self.button(advanced,'打开本地数据目录',lambda:os.startfile(self.data_dir)).pack(anchor='w',pady=12)

    def build_help(self):
        self.help_view=GuideView(self.help_tab,HELP_TEXT)
        self.help_view.pack(fill='both',expand=True)

    def current_settings(self):
        return Settings(self.adb.get().strip(), self.serial.get().strip(), self.account.get().strip(), self.folder.get().strip())

    def context_changed(self, *_):
        self.last_duplicates = []
        self.trial_item_id, self.trial_context = None, ''
        self.trial_status.set('账号或设备已变更，请在当前环境选择新图片验证。')
        if hasattr(self, 'wechat_panel'):
            self.wechat_panel.invalidate()
        if hasattr(self, 'exception_panel'):
            self.exception_panel.reset_context()
        self.confirmed.set(False)
        self.auto.set(False)
        self.refresh_profile()
        self.reload_rows()

    def profile_context(self):
        s = self.current_settings()
        return self.profile.verification_context(s.account, s.serial, s.adb)

    def save_settings(self):
        self.current_settings().save(self.settings_path)

    def make_device(self):
        if not self.serial.get() or not self.adb.get():
            raise ValueError('请在“模拟器连接”中选择设备。')
        return AndroidDevice(Adb(self.adb.get()), self.serial.get())

    def background(self, task, callback=None, after_idle=None):
        if self.busy:
            return
        self.save_settings()
        self.set_busy(True)
        def work():
            completed = False
            try:
                value = task()
                if callback:
                    self.events.put(('callback', (callback, value)))
                completed = True
            except Exception as exc:
                self.events.put(('error', str(exc)))
            finally:
                self.events.put(('idle', None))
                if completed and after_idle:
                    self.events.put(('callback', (lambda _: after_idle(), None)))
        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def set_busy(self, busy):
        self.busy = busy
        self.pause_button.configure(text='暂停任务', state='normal' if busy else 'disabled')
        for widget in self.controls:
            widget.configure(state='disabled' if busy else ('readonly' if isinstance(widget, ttk.Combobox) else 'normal'))
        if hasattr(self,'wechat_panel'):
            self.wechat_panel.refresh_home()
        if hasattr(self,'exception_panel'):
            self.exception_panel.refresh_actions()
        if hasattr(self,'setup_panel'):
            self.setup_panel.refresh()

    def poll(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == 'callback':
                    callback, value = payload
                    callback(value)
                elif kind == 'idle':
                    self.trial_panel.running=False
                    self.wechat_panel.active = False
                    self.exception_panel.retry_in_progress = False
                    self.set_busy(False)
                    self.reload_rows()
                elif kind in {'wechat-progress', 'cleanup'}:
                    self.wechat_panel.progress.set(payload)
                    self.status.set(payload)
                else:
                    if self.exception_panel.retry_in_progress and kind in {'blocked', 'error', 'needs-review', 'completed', 'update'}:
                        self.exception_panel.retry_notice.set(payload)
                    if self.trial_panel.running:
                        self.trial_panel.message.set(payload)
                    if kind in {'blocked', 'error', 'needs-review', 'completed', 'update'}:
                        self.action_notice.set(payload)
                    if self.wechat_panel.active:
                        self.wechat_panel.progress.set(payload)
                    self.status.set(payload)
                    self.reload_rows()
        except queue.Empty:
            pass
        except Exception as exc:
            self.status.set(f'界面更新失败：{exc}')
        self.poll_id = self.root.after(120, self.poll)

    def choose_folder(self):
        path = filedialog.askdirectory(parent=self.root)
        if path:
            self.folder.set(path)

    def show_setup(self, step=0):
        self.tabs.select(self.settings_tab)
        self.setup_panel.steps.select(step)
        self.setup_panel.refresh()

    def add_local_images(self):
        if self.busy:
            return
        if not self.account.get().strip():
            self.show_setup(1)
            self.status.set('请先给目标小红书账号起一个名称，再添加图片。')
            return
        path=filedialog.askdirectory(parent=self.root,title='选择表情图片文件夹')
        if path:
            self.folder.set(path)
            self.tabs.select(self.import_tab)
            self.scan()

    def start_product_import(self, selected_only=True):
        if self.busy:
            return
        if not self.confirmed.get():
            self.show_setup(1)
            self.status.set('请先确认当前微信来源和小红书目标账号。')
            return
        rows=[self.rows_cache[k] for k in self.tree.selection()] if selected_only else [
            r for r in self.rows_cache.values() if r['status']=='pending' and not r.get('recovery_queued')]
        if not rows:
            self.action_notice.set('请先选中需要导入的图片。' if selected_only else '没有普通待处理图片。')
            return
        settings=self.current_settings()
        error=start_problem(settings,self.profile,True,True)
        if error:
            self.show_setup(0 if not settings.adb or not settings.serial else 2)
            self.status.set(error)
            self.action_notice.set(error)
            self.trial_status.set(error)
            return
        ready=[r for r in rows if r['format'] in self.profile.tested_formats]
        held=[r for r in rows if r['format'] not in self.profile.tested_formats]
        missing='、'.join(sorted({r['format'] for r in held}))
        if not ready:
            reason=f'所选 {len(held)} 张图片的格式（{missing}）尚未验证；请为这些格式各试导一张并确认手机同步。已有验证记录保留。'
            self.show_setup(2)
            self.status.set(reason)
            self.action_notice.set(reason)
            self.trial_status.set(reason)
            return
        # Dispatch explicit IDs so unverified formats never enter the engine or
        # turn into failed/skipped attempts. Preserve their existing queue state.
        self.tree.selection_set([str(r['id']) for r in ready])
        self.row_detail()
        self.selection_notice.set(
            f'本批选择 {len(ready)} 张已验证格式；另 {len(held)} 张 {missing} 尚未验证，本次不导入，保留在列表中。'
            if held else f'本批选择 {len(ready)} 张图片，所选格式均已验证。')
        self.auto.set(True)
        self.start(None,selected_only=True)

    def select_all_pending(self):
        if self.busy:
            return
        keys=[key for key,row in self.rows_cache.items()
              if row['status']=='pending' and not row.get('recovery_queued')]
        self.tree.selection_set(keys)
        if keys:
            self.tree.focus(keys[0])
            self.tree.see(keys[0])
        self.row_detail()
        self.selection_notice.set(f'已选中 {len(keys)} 张普通待处理图片；导入时先处理已验证格式，其他格式保留。')
        self.action_notice.set(
            f'已选中 {len(keys)} 张待处理图片，点击“导入选中”开始。异常及待重试图片请到“异常处理”操作。'
            if keys else '没有普通待处理图片；失败、跳过、待核对和待重试图片请到“异常处理”操作。')

    def prepare_profile(self, on_ready=None):
        if self.busy:
            return
        if self.calibration_window is not None:
            self.trial_status.set('请先关闭自定义界面适配窗口，再检测小红书。')
            return
        try:
            device=self.make_device()
        except ValueError as exc:
            self.show_setup(0)
            self.status.set(str(exc))
            return
        original=copy.deepcopy(self.profile)
        context=self.profile_context()
        def task():
            from .xhs_adapter import PACKAGE, make_profile
            identity=device.identity(PACKAGE)
            try:
                original.validate()
                if original.package==PACKAGE and original.version==identity['version']:
                    return original
            except (ValueError,TypeError):
                pass
            return make_profile(identity['version'])
        def done(profile):
            if self.profile_context()!=context:
                self.status.set('配置环境已变化，请重新检测。')
                return
            profile.save(self.data_dir/'profile.json')
            self.profile=profile
            self.refresh_profile()
            self.trial_status.set('小红书已准备好。保持收藏面板打开，选择一张未收藏的图片试导。')
        if on_ready is None:
            self.background(task,done)
        else:
            self.background(task,done,after_idle=on_ready)

    def trial_selected_file(self):
        if self.busy:
            return
        error=start_problem(self.current_settings(),self.profile,self.confirmed.get())
        if error:
            self.trial_status.set(error+' 先完成前两步，再检测并准备小红书。')
            return
        path=filedialog.askopenfilename(parent=self.root,title='选择尚未收藏的表情',filetypes=[('表情图片','*.png *.jpg *.jpeg *.gif *.webp')])
        if not path:
            return
        from .catalog import inspect_file
        item=inspect_file(Path(path))
        if item.error:
            self.trial_status.set(item.error)
            return
        self.store.add(self.account.get().strip(),[item])
        self.reload_rows()
        row=next(r for r in self.store.rows(self.account.get().strip()) if r['digest']==item.digest)
        if row.get('setup_sample'):
            self.trial_status.set('这是一张自动测试图，请使用上方“继续未完成试导”，或核对后换一张测试图。')
            return
        if row['status']!='pending':
            self.trial_status.set('这张图片已有处理记录，请换一张尚未收藏的图片；异常记录可到异常处理核对。')
            return
        self.trial_item_id,self.trial_context=row['id'],self.profile_context()
        self.trial_status.set('正在试导：'+Path(row['path']).name+'。结束后请去手机核对。')
        self.tree.selection_set(str(row['id']))
        self.tree.focus(str(row['id']))
        self.auto.set(False)
        self.start(1)

    def current_trial(self):
        if not hasattr(self,'store') or self.trial_context!=self.profile_context():
            return None
        return next((r for r in self.store.rows(self.account.get().strip()) if r['id']==self.trial_item_id
                     and r['attempt_context']==self.trial_context and r['attempt_kind']=='trial'),None)

    def confirm_trial_sync(self):
        if self.busy:
            return
        row=self.current_trial()
        if not self.confirmed.get() or row is None or row['status'] not in {'uncertain','success'}:
            self.trial_status.set('没有可确认的本次试导；请先在当前账号和设备完成一张试导。')
            return
        if row['status']=='uncertain':
            self.store.resolve_many([row['id']],self.account.get().strip(),True)
        context=self.profile_context()
        verified=self.store.verified_formats(self.account.get().strip(),context,require_visual=bool(self.profile.verifier))
        current_proof = row['attempt_submitted'] and (not self.profile.verifier or row['attempt_visual'])
        if current_proof and row['format'] in verified:
            existing=set(self.profile.tested_formats) if self.profile.tested_context==context else set()
            self.profile.tested_formats=sorted(existing|{row['format']})
            self.profile.tested=True
            self.profile.phone_sync=True
            self.profile.tested_context=context
            self.profile.save(self.data_dir/'profile.json')
            self.trial_status.set(f"{row['format']} 已通过验证，可自动导入。需要其他格式时，再选择该格式的新图片试导。")
        else:
            self.trial_status.set('已记录本张收藏成功；程序未取得自动核对证据，此格式仍需逐张核对。可换一张图片再验证。')
        self.trial_item_id,self.trial_context=None,''
        self.refresh_profile()
        self.reload_rows()
        self.cleanup_confirmed(item_id=row['id'])

    def choose_adb(self):
        path = filedialog.askopenfilename(parent=self.root, filetypes=[('ADB', 'adb.exe'), ('程序', '*.exe')])
        if path:
            self.adb.set(path)

    def scan(self):
        settings = self.current_settings()
        if not settings.account:
            messagebox.showinfo('账号标签', '先填写用于区分任务记录的账号标签，例如“我的小红书”。', parent=self.root)
            return
        def task():
            items = scan_folder(Path(settings.folder))
            store = Store(self.db_path)
            try:
                duplicates = []
                counts = store.add(settings.account, items, duplicates=duplicates)
            finally:
                store.close()
            invalid = [{'path': str(i.path), 'error': i.error} for i in items if i.error]
            (self.data_dir / 'scan-errors.json').write_text(json.dumps(invalid, ensure_ascii=False, indent=2), encoding='utf-8')
            (self.data_dir / 'scan-duplicates.json').write_text(json.dumps(
                {'account': settings.account, 'duplicates': duplicates}, ensure_ascii=False, indent=2), encoding='utf-8')
            return counts, invalid, duplicates
        def done(result):
            (added, dup, bad), invalid, self.last_duplicates = result
            self.task_images.refresh()
            self.gallery.refresh()
            self.exception_panel.images.refresh()
            self.reload_rows()
            saved = sum(d['status'] == 'success' for d in self.last_duplicates)
            self.status.set(f'扫描完成：新增 {added} 张，重复 {dup} 张（其中已收藏 {saved} 张），无法读取 {bad} 张。重复项保留原任务状态，可点击“重复明细”查看。')
            if invalid:
                messagebox.showwarning('以下图片未加入任务', '\n'.join(Path(i['path']).name for i in invalid[:12]) + '\n详细原因保存在数据目录 scan-errors.json。', parent=self.root)
        self.status.set('正在读取图片和动图，请稍候…')
        self.background(task, done)

    def show_duplicates(self):
        if not self.last_duplicates:
            messagebox.showinfo('重复明细', '本次扫描没有重复项，或尚未扫描。\n同账号按文件内容识别，改名或换目录仍可识别。\n只能查询本工具保留的任务记录，无法遍历小红书中以前手动收藏的全部表情。', parent=self.root)
            return
        window = tk.Toplevel(self.root)
        window.title('本次扫描 · 重复图片')
        window.geometry('880x420')
        ttk.Label(window, text='以下图片未新建任务。沿用已有状态；“待核对/跳过”不代表已经收藏。', padding=12).pack(anchor='w')
        frame = ttk.Frame(window, padding=12)
        frame.pack(fill='both', expand=True)
        table = ttk.Treeview(frame, columns=('file', 'status', 'original'), show='headings')
        for key, label, width in [('file', '本次文件', 300), ('status', '已有状态', 100), ('original', '已有记录的文件', 400)]:
            table.heading(key, text=label)
            table.column(key, width=width)
        scroll = ttk.Scrollbar(frame, orient='vertical', command=table.yview)
        table.configure(yscrollcommand=scroll.set)
        table.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        for item in self.last_duplicates:
            table.insert('', 'end', values=(Path(item['path']).name, LABELS[item['status']], item['existing_path']))

    def reload_rows(self):
        if not hasattr(self, 'store'):
            return
        selected = self.tree.selection()
        focused = self.tree.focus()
        position = self.tree.yview()[0]
        self.rows_cache = {str(r['id']): r for r in self.store.rows(self.account.get().strip()) if not r.get('setup_sample')}
        self.tree.delete(*self.tree.get_children())
        for key, row in self.rows_cache.items():
            self.tree.insert('', 'end', iid=key, values=(Path(row['path']).name, f"{row['format']} · {row['frames']} 帧", LABELS[row['status']]), tags=(row['status'],))
        if selected:
            self.tree.selection_set([key for key in selected if key in self.rows_cache])
        if focused in self.rows_cache:
            self.tree.focus(focused)
        self.tree.yview_moveto(position)
        self.task_images.update_rows(self.rows_cache)
        self.gallery.update_rows(self.rows_cache)
        self.row_detail()
        counts = {state: sum(r['status'] == state for r in self.rows_cache.values()) for state in LABELS}
        self.counts.set(f"共 {len(self.rows_cache)} 张    待处理 {counts['pending']}    已收藏 {counts['success']}    待核对 {counts['uncertain']}    失败 {counts['failed']}    已跳过 {counts['skipped']}")
        self.refresh_batch_progress()
        self.exception_panel.reload()
        self.wechat_panel.refresh_home()
        self.setup_panel.refresh()

    def refresh_batch_progress(self):
        summary = self.batch_progress.summary(self.account.get().strip(), self.rows_cache.values()) if self.batch_progress else None
        if summary is None or not summary.total:
            self.batch_frame.pack_forget()
            return
        self.batch_text.set(f'本批 已收藏 {summary.success} / {summary.total} · 未成功 {summary.remaining} '
                            f'（失败 {summary.failed} · 跳过 {summary.skipped} · 待核对 {summary.uncertain}）')
        self.batch_bar['value'] = summary.percent
        if not self.batch_frame.winfo_manager():
            self.batch_frame.pack(fill='x', pady=(0, 10), before=self.tabs)

    def selected_row(self):
        sel = self.tree.selection()
        focused = self.tree.focus()
        return self.rows_cache.get(focused if focused in sel else sel[0]) if sel else None

    def row_detail(self, _=None):
        row = self.selected_row()
        if row:
            note = '上次任务记录：' + row['note'] if row['note'] else '保留原始文件，不转换格式。'
            self.detail.set(f"{row['path']}\n{row['format']} · {row['frames']} 帧 · {row['width']}×{row['height']}\n{note}")
        else:
            self.detail.set('选中一张图片可查看预览和说明；点击“全选待导入”可选中剩余待处理图片。')
        self.task_images.show(row)

    def discover(self):
        adb = Adb(self.adb.get())
        def done(devices):
            online = [d.serial for d in devices if d.state == 'device']
            self.devices.configure(values=online)
            if self.serial.get() not in online:
                self.serial.set('')
            self.confirmed.set(False)
            self.device_note.set('\n'.join(f'{d.serial}  [{d.state}]  {d.detail}' for d in devices) or '没有发现设备。请启动模拟器并开启 ADB 本地连接。')
            self.status.set('设备列表已更新，请明确选择目标设备。')
        self.background(adb.devices, done)

    def connect_endpoint(self):
        adb, endpoint = Adb(self.adb.get()), self.endpoint.get().strip()
        self.background(lambda: adb.connect(endpoint), lambda text: self.device_note.set(text + '\n随后点击“刷新设备”。'))

    def start(self, limit, selected_only=False):
        if self.busy:
            return
        if self.calibration_window is not None:
            messagebox.showinfo('请先完成适配', '关闭界面适配窗口后再启动任务。', parent=self.root)
            return
        settings, auto = self.current_settings(), self.auto.get()
        skip_errors = self.skip_errors.get()
        error = start_problem(settings, self.profile, self.confirmed.get(), auto)
        if error:
            messagebox.showinfo('尚不能开始导入', error, parent=self.root)
            return
        if not auto and limit != 1:
            messagebox.showinfo('逐张核对模式', '当前每张添加后都会暂停。完成实测和结果定位后可开启自动核对。', parent=self.root)
        device = self.make_device()
        profile = Profile.load(self.data_dir / 'profile.json')
        item_ids = [self.rows_cache[key]['id'] for key in self.tree.selection()] if selected_only else None
        if selected_only and not item_ids:
            messagebox.showinfo('请选择图片', '点击“全选待导入”，或用 Ctrl / Shift 选择本批图片，再点击“导入选中”。', parent=self.root)
            return
        selected = self.selected_row() if limit == 1 and not selected_only else None
        item_id = selected['id'] if selected else None
        self.launch_import(settings, device, profile, limit, auto, item_id=item_id, item_ids=item_ids, skip_errors=skip_errors,
                           track_batch=bool(selected_only and limit is None))

    def launch_import(self, settings, device, profile, limit, auto, *, item_id=None, item_ids=None, skip_errors=None, trial_batch_ids=None, track_batch=False):
        if self.busy:
            return
        if skip_errors is None:
            skip_errors = self.skip_errors.get()
        item_ids = list(item_ids) if item_ids is not None else None
        trial_batch_ids=list(trial_batch_ids) if trial_batch_ids is not None else None
        if track_batch and item_ids:
            self.batch_progress = BatchProgress.capture(settings.account, item_ids, self.store.rows(settings.account))
            self.refresh_batch_progress()
        self.action_notice.set('正在检查收藏起始页；确认页面后开始本批导入。')
        self.stop_event.clear()
        def task():
            store = Store(self.db_path)
            try:
                engine = ImportEngine(store, device, profile, settings.account, lambda kind, msg: self.events.put((kind, msg)), self.stop_event, connection=settings.adb)
                engine.run(limit, auto, item_id=item_id, item_ids=item_ids,
                           skip_errors=False if trial_batch_ids is not None else skip_errors, trial_batch_ids=trial_batch_ids)
            finally:
                store.close()
        self.background(task)

    def stop_task(self):
        if not self.busy:
            return
        self.stop_event.set()
        self.pause_button.configure(text='正在暂停…', state='disabled')
        self.status.set('正在暂停；已提交但未核对的图片将保留为“待核对”。')

    def resolve(self, imported):
        row = self.selected_row()
        if not row:
            return
        try:
            self.store.resolve(row['id'], imported)
            self.reload_rows()
            if imported:
                self.cleanup_confirmed(item_id=row['id'])
        except ValueError as exc:
            messagebox.showinfo('任务核对', str(exc), parent=self.root)

    def cleanup_confirmed(self, item_id=None, on_done=None):
        settings = self.current_settings()
        if not settings.serial or not settings.adb:
            if on_done:
                on_done()
            return
        self.stop_event.clear()
        def clean():
            store = Store(self.db_path)
            try:
                device = AndroidDevice(Adb(settings.adb), settings.serial)
                return device.cleanup_staging(store, settings.account, self.stop_event,
                    lambda text:self.events.put(('cleanup',text)), discover=True, item_id=item_id)
            finally:
                store.close()
        self.background(clean, after_idle=on_done)

    def skip_selected(self):
        selected = [self.rows_cache[key] for key in self.tree.selection()]
        eligible = [r for r in selected if r['status'] in {'pending','failed','uncertain'}]
        if not eligible:
            messagebox.showinfo('请选择任务', '选中待处理、失败或待核对图片后，可跳过本次导入。', parent=self.root)
            return
        try:
            for row in eligible:
                self.store.skip(row['id'], self.account.get().strip())
            self.reload_rows()
            self.status.set(f'已跳过 {len(eligible)} 张。可继续其余待处理项；跳过项以后仍可核对或重新排队。')
        except ValueError as exc:
            messagebox.showinfo('跳过任务', str(exc), parent=self.root)

    def calibrate(self):
        if self.calibration_window is not None:
            self.calibration_window.lift()
            return
        from .calibration import Calibration
        self.calibration_window = Calibration(self)

    def refresh_profile(self):
        ready = self.profile.tested and self.profile.phone_sync and self.profile.tested_context == self.profile_context()
        self.profile_status.set(f"小红书版本：{self.profile.version or '尚未检测'}\n" +
            ('已验证格式：'+'、'.join(self.profile.tested_formats) if ready else '尚未完成首次验证：先自动试导，再核对手机同步。'))
        for fmt, var in self.test_formats.items():
            var.set(ready and fmt in self.profile.tested_formats)
        self.sync_checked.set(bool(ready))
        if not ready:
            self.auto.set(False)
        if hasattr(self,'wechat_panel'):
            self.wechat_panel.refresh_home()
        if hasattr(self,'setup_panel'):
            self.setup_panel.refresh()

    def import_profile(self):
        path = filedialog.askopenfilename(parent=self.root, filetypes=[('界面配置', '*.json')])
        if path:
            try:
                p = Profile.load(path)
                p.validate()
                p.invalidate_verification()
                p.save(self.data_dir / 'profile.json')
                self.profile = p
                self.refresh_profile()
            except (ValueError, TypeError, OSError) as exc:
                messagebox.showerror('配置导入失败', str(exc), parent=self.root)

    def load_xhs_preset(self):
        if self.calibration_window is not None:
            messagebox.showinfo('请先关闭向导', '关闭界面适配向导后再加载内置适配。', parent=self.root)
            return
        from .xhs_adapter import PACKAGE, make_profile
        device = self.make_device()
        def task():
            profile = make_profile(device.identity(PACKAGE)['version'])
            profile.validate()
            return profile
        def done(profile):
            profile.save(self.data_dir / 'profile.json')
            self.profile = profile
            self.refresh_profile()
            self.status.set('已加载内置适配。返回收藏表情页，选中一张待处理图片后点击“试导1张”。')
        self.background(task, done)

    def export_profile(self):
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension='.json', initialfile='小红书界面配置.json')
        if path:
            self.profile.save(path)

    def mark_tested(self):
        error = start_problem(self.current_settings(), self.profile, self.confirmed.get())
        if error:
            messagebox.showinfo('先确认当前环境', error, parent=self.root)
            return
        formats = [fmt for fmt, var in self.test_formats.items() if var.get()]
        verified = self.store.verified_formats(self.account.get().strip(), self.profile_context(), require_visual=bool(self.profile.verifier))
        if not formats or not self.sync_checked.get() or not set(formats).issubset(verified):
            messagebox.showinfo('先完成实测', '所选格式需要在当前环境试导并核对成功；视觉配置还要求程序实际识别到双重证据。检查手机同步后再记录。历史或手动演示记录不能代替本次试导。', parent=self.root)
            return
        try:
            self.profile.validate()
        except ValueError as exc:
            messagebox.showinfo('界面未适配', str(exc), parent=self.root)
            return
        self.profile.tested = True
        self.profile.phone_sync = True
        self.profile.tested_formats = formats
        self.profile.tested_context = self.profile_context()
        self.profile.save(self.data_dir / 'profile.json')
        self.refresh_profile()

    def export_csv(self):
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension='.csv', initialfile='表情导入结果.csv')
        if not path:
            return
        with open(path, 'w', newline='', encoding='utf-8-sig') as stream:
            writer = csv.writer(stream)
            writer.writerow(['文件', '格式', '帧数', '状态', '说明'])
            for r in self.rows_cache.values():
                values = [r['path'], r['format'], str(r['frames']), LABELS[r['status']], r['note']]
                writer.writerow(["'" + v if v.startswith(('=', '+', '-', '@')) else v for v in values])
        self.status.set('结果已导出。')

    def close(self):
        if self.busy:
            self.stop_task()
            messagebox.showinfo('任务正在停止', '请等待当前操作退出后再关闭窗口，任务进度会保留。', parent=self.root)
            return
        self.save_settings()
        self.root.after_cancel(self.poll_id)
        self.wechat_panel.close()
        self.gallery.close()
        self.task_images.close()
        self.exception_panel.close()
        self.store.close()
        self.root.destroy()
