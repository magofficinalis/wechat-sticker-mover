"""Account-scoped review and retry workbench; all widget work stays on Tk."""
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox
from tkinter.scrolledtext import ScrolledText

from .config import start_problem
from .preview_widgets import TaskImages
from .profile import Profile
from .widgets import OverflowScrollbar

LABELS = {'failed':'失败', 'skipped':'已跳过', 'uncertain':'待核对',
          'pending':'待重试', 'running':'处理中', 'submitting':'添加中'}
FILTERS = {'全部异常':None, '已跳过':'skipped', '失败':'failed', '待核对':'uncertain', '待重试':'pending'}
MANUAL = '逐张人工核对'


class ExceptionPanel:
    def __init__(self, app, parent):
        self.app, self.parent = app, parent
        self.rows, self.account_scope = {}, None
        self.batch_ids, self.batch_context = [], ''
        self.retry_in_progress = False
        self.controls = []
        self.filter = tk.StringVar(value='全部异常')
        self.search = tk.StringVar()
        self.mode = tk.StringVar(value=MANUAL)
        self.summary = tk.StringVar()
        self.selection_note = tk.StringVar()
        self.retry_notice = tk.StringVar(value='')
        ttk.Label(parent, textvariable=self.summary, style='Section.TLabel').pack(anchor='w')
        ttk.Label(parent, text='先核对收藏结果，再批量加入重试队列。这里只处理异常队列中的图片。', style='Muted.TLabel').pack(anchor='w', pady=(5,8))
        bar = ttk.Frame(parent)
        bar.pack(fill='x', pady=(0,8))
        self.combo(bar, self.filter, list(FILTERS), 12).pack(side='left')
        ttk.Label(bar, text='搜索').pack(side='left', padx=(10,4))
        entry = ttk.Entry(bar, textvariable=self.search, width=22)
        entry.pack(side='left', fill='x', expand=True)
        self.register(entry)
        self.button(bar, '全选当前结果', self.select_all).pack(side='left', padx=6)
        self.more = self.register(ttk.Menubutton(bar, text='更多'))
        menu = tk.Menu(self.more, tearoff=False)
        menu.add_command(label='暂不重试选中', command=self.park_selected)
        self.more.configure(menu=menu)

        footer = ttk.Frame(parent)
        footer.pack(side='bottom', fill='x')
        content = ttk.Frame(parent)
        content.pack(fill='both', expand=True)
        side = ttk.Frame(content, width=216, padding=(12,0,0,0))
        side.pack(side='right', fill='y')
        side.pack_propagate(False)
        table = ttk.Frame(content)
        table.pack(side='left', fill='both', expand=True)
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=('file','format','status','reason'),
            show='tree headings', selectmode='extended', style='Sticker.Treeview', height=3)
        self.tree.heading('#0', text='预览')
        self.tree.column('#0', width=86, minwidth=86, stretch=False)
        for key, label, width in [('file','文件',180),('format','格式 / 帧数',105),
                ('status','状态',75),('reason','原因 / 进度',260)]:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=70, stretch=key in {'file','reason'})
        scroll = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        horizontal = OverflowScrollbar(table, orient='horizontal', command=self.tree.xview)
        self.tree.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        self.tree.grid(row=0,column=0,sticky='nsew')
        scroll.grid(row=0,column=1,sticky='ns')
        horizontal.grid(row=1,column=0,sticky='ew')
        self.tree.tag_configure('failed', foreground='#b52d45')
        self.tree.tag_configure('uncertain', foreground='#a66c00')
        self.images = TaskImages(app.root, self.tree, side)
        self.tree.bind('<<TreeviewSelect>>', self.row_detail)
        self.tree.bind('<Configure>', lambda _: self.tree.after_idle(self.keep_selection_visible))
        ttk.Label(footer, textvariable=self.selection_note, wraplength=920).pack(anchor='w', pady=(8,4))
        self.detail = ScrolledText(footer, height=3, wrap='word', font=('Microsoft YaHei UI',10), state='disabled')
        self.detail.pack(fill='x')
        first = ttk.Frame(footer)
        first.pack(fill='x', pady=(8,5))
        self.collected_button = self.button(first, '选中已收藏', lambda:self.resolve_selected(True))
        self.requeue_button = self.button(first, '选中未收藏，加入重试', lambda:self.resolve_selected(False))
        self.confirm_button = self.button(first, '确认本张已收藏并继续', self.confirm_and_continue)
        second = ttk.Frame(footer)
        second.pack(fill='x', pady=(0,5))
        self.mode_combo = self.combo(second, self.mode, [MANUAL,'自动核对'], 15)
        self.retry_button = self.button(second, '重试', self.retry_action, style='Accent.TButton')
        ttk.Label(footer, textvariable=self.retry_notice, wraplength=920).pack(anchor='w', pady=(0,5))
        ttk.Label(footer, text='逐张模式每张添加后暂停，确认手机同步及动画后继续；自动模式仍需已有实测资格。', style='Muted.TLabel').pack(anchor='w')
        self.filter.trace_add('write', lambda *_:self.reload())
        self.search.trace_add('write', lambda *_:self.reload())
        self.mode.trace_add('write', lambda *_:self.refresh_actions())
        self.refresh_actions()

    def register(self, widget):
        self.controls.append(widget)
        self.app.controls.append(widget)
        return widget

    def reset_context(self):
        self.account_scope = None
        self.batch_ids, self.batch_context = [], ''
        self.retry_in_progress = False
        self.retry_notice.set('')
        self.mode.set(MANUAL)

    def button(self, parent, text, command, **kwargs):
        return self.register(ttk.Button(parent, text=text, command=command, **kwargs))

    def combo(self, parent, variable, values, width):
        return self.register(ttk.Combobox(parent, textvariable=variable, values=values, width=width, state='readonly'))

    def reload(self):
        if not hasattr(self.app, 'store'):
            return
        account = self.app.account.get().strip()
        selected = self.tree.selection() if account == self.account_scope else ()
        focused = self.tree.focus() if account == self.account_scope else ''
        position = self.tree.yview()[0] if account == self.account_scope else 0
        self.account_scope = account
        all_rows = self.app.store.exception_rows(account)
        state, query = FILTERS[self.filter.get()], self.search.get().strip().casefold()
        self.rows = {str(r['id']):r for r in all_rows if (state is None or r['status']==state)
            and (not query or query in (Path(r['path']).name+' '+r['note']+' '+r['format']).casefold())}
        self.tree.delete(*self.tree.get_children())
        for key, row in self.rows.items():
            reason = ' '.join(row['note'].split())
            brief = reason if len(reason)<=48 else reason[:47]+'…'
            self.tree.insert('', 'end', iid=key, values=(Path(row['path']).name,
                f"{row['format']} · {row['frames']} 帧", LABELS[row['status']], brief), tags=(row['status'],))
        self.tree.selection_set([key for key in selected if key in self.rows])
        if focused in self.rows:
            self.tree.focus(focused)
        self.tree.yview_moveto(position)
        self.images.update_rows(self.rows)
        self.row_detail()
        counts = {s:sum(r['status']==s for r in all_rows) for s in LABELS}
        self.summary.set(f"账号：{account or '未选择'}    已跳过 {counts['skipped']}    失败 {counts['failed']}    待核对 {counts['uncertain']}    待重试 {counts['pending']}")
        self.app.tabs.tab(self.parent, text=f'异常处理（{len(all_rows)}）')

    @staticmethod
    def submission(row):
        if row.get('setup_sample'):
            return '首次验证测试图；核对结果后，返回“首次验证”继续试导或换图'
        if row['status']=='pending' and row['recovery_queued']:
            return '用户已确认未收藏'
        if row['attempt_submitted']:
            return '可能已添加，请先核对'
        if row['attempt_context'] and row['attempt_kind'] in {'trial','auto'}:
            return '本次尚未提交'
        return '需核对收藏结果'

    def selected_ids(self):
        return [self.rows[key]['id'] for key in self.tree.selection() if key in self.rows]

    def select_all(self):
        self.tree.selection_set(self.tree.get_children())
        self.row_detail()

    def row_detail(self, _=None):
        selected = self.tree.selection()
        focus = self.tree.focus()
        row = self.rows.get(focus if focus in selected else selected[0]) if selected else None
        text = (f"{row['path']}\n{self.submission(row)}\n{row['note']}" if row else '选中图片可查看完整原因。已跳过不代表已收藏；核对后再决定重试。')
        self.detail.configure(state='normal')
        self.detail.delete('1.0','end')
        self.detail.insert('1.0',text)
        self.detail.configure(state='disabled')
        self.images.show(row)
        self.refresh_actions()

    def refresh_actions(self):
        selected = [self.rows[key] for key in self.tree.selection() if key in self.rows]
        states = {row['status'] for row in selected}
        review = bool(selected) and states <= {'failed','skipped','uncertain'}
        retry = bool(selected) and all(row['status']=='pending' and row['recovery_queued'] for row in selected)
        samples = sum(bool(row.get('setup_sample')) for row in selected)
        uncertain = len(selected)==1 and states=={'uncertain'}
        queued = (sum(row['status']=='pending' and row['recovery_queued'] and not row.get('setup_sample')
            for row in self.app.store.exception_rows(self.app.account.get().strip()))
            if hasattr(self.app, 'store') else 0)
        if selected:
            if samples:
                scope = '测试图请回到首次验证继续。' if samples==len(selected) else '请分开选择测试图和普通图片。'
            else:
                scope = f'本次仅重试选中的 {len(selected)} 张。' if retry else '选中图片须先核对收藏结果，再加入重试。'
            label = '继续首次验证' if samples==len(selected) else f'重试选中（{len(selected)} 张）'
        else:
            scope = f'将重试当前账号队列全部 {queued} 张（不受筛选影响）。' if queued else '选中图片可查看完整原因。'
            label = f'重试队列（{queued} 张）'
        self.selection_note.set(f'显示 {len(self.rows)} 张 · 已选 {len(selected)} 张 · {scope}')
        self.retry_button.configure(text=label)
        for widget in (self.collected_button, self.requeue_button, self.confirm_button,
                self.mode_combo, self.retry_button, self.more):
            widget.pack_forget()
        if review:
            self.collected_button.pack(side='left')
            self.requeue_button.pack(side='left', padx=6)
        if uncertain and self.mode.get()==MANUAL:
            self.confirm_button.pack(side='left')
        can_retry = (retry and samples in (0,len(selected))) or (not selected and queued)
        if (can_retry and not samples) or uncertain:
            self.mode_combo.pack(side='left')
        if can_retry:
            self.retry_button.pack(side='left', padx=6)
        if selected and states <= {'failed','skipped','uncertain','pending'}:
            self.more.pack(side='left')

    def keep_selection_visible(self, _=None):
        selected=self.tree.selection()
        if selected:
            focused=self.tree.focus()
            self.tree.see(focused if focused in selected else selected[0])

    def resolve_selected(self, imported):
        if self.app.busy:
            return
        ids = self.selected_ids()
        try:
            self.app.store.resolve_many(ids, self.app.account.get().strip(), imported)
        except ValueError as exc:
            messagebox.showinfo('异常任务核对', str(exc), parent=self.app.root)
            return
        if not imported:
            self.search.set('')
            self.filter.set('待重试')
        self.app.reload_rows()
        if not imported:
            self.tree.selection_set([str(i) for i in ids if str(i) in self.rows])
            self.row_detail()
        self.app.status.set(f"已将 {len(ids)} 张标记为已收藏。" if imported else f'已将 {len(ids)} 张加入异常重试队列。选择重试方式后点击“重试选中”。')
        if imported:
            self.app.cleanup_confirmed()

    def park_selected(self):
        if self.app.busy:
            return
        try:
            ids = self.selected_ids()
            self.app.store.park_many(ids, self.app.account.get().strip())
            self.app.reload_rows()
            self.app.status.set(f'已暂存 {len(ids)} 张为已跳过；原文件和任务记录保留。')
        except ValueError as exc:
            messagebox.showinfo('暂不重试', str(exc), parent=self.app.root)

    def start_retry(self, all_queued=False, *, batch_ids=None):
        app = self.app
        if app.busy:
            return
        if app.calibration_window is not None:
            messagebox.showinfo('请先关闭适配向导', '关闭界面适配向导后再重试。', parent=app.root)
            return
        settings, auto = app.current_settings(), self.mode.get()=='自动核对'
        if not all_queued and batch_ids is None and any(self.rows[str(i)].get('setup_sample') for i in self.selected_ids()):
            app.show_setup(2)
            app.trial_panel.message.set('测试图只在首次验证中重试。已确认未收藏的图片，可点击“继续未完成试导”。')
            return
        queued = {r['id']:r for r in app.store.exception_rows(settings.account)
            if r['status']=='pending' and r['recovery_queued'] and not r.get('setup_sample')}
        ids = list(batch_ids) if batch_ids is not None else (list(queued) if all_queued else self.selected_ids())
        if not ids or any(i not in queued for i in ids):
            messagebox.showinfo('先核对收藏结果', '请先选中异常图片，确认未收藏后点击“选中未收藏，加入重试”。重试只处理队列中的待重试项。', parent=app.root)
            return
        error = start_problem(settings, app.profile, app.confirmed.get(), auto)
        if error:
            messagebox.showinfo('尚不能重试', error, parent=app.root)
            return
        try:
            profile, device = Profile.load(app.data_dir/'profile.json'), app.make_device()
        except (OSError, ValueError, TypeError) as exc:
            self.retry_notice.set(f'无法开始重试：{exc}。请检查模拟器连接和界面配置。')
            app.status.set(self.retry_notice.get())
            return
        self.batch_ids, self.batch_context = list(ids), app.profile_context()
        self.search.set('')
        # Keep the active item visible after its status changes to waiting for review.
        self.filter.set('全部异常')
        self.tree.selection_set([str(ids[0])])
        self.tree.focus(str(ids[0]))
        self.tree.see(str(ids[0]))
        self.retry_in_progress = True
        self.retry_notice.set('正在检查模拟器的收藏面板，请稍候。')
        try:
            app.launch_import(settings, device, profile, None, auto, item_ids=ids)
        except (OSError, ValueError, TypeError) as exc:
            self.retry_in_progress = False
            self.retry_notice.set(f'启动重试时出错：{exc}。请先核对收藏结果，再决定是否重试。')
            app.status.set(self.retry_notice.get())

    def confirm_and_continue(self):
        app = self.app
        if app.busy:
            return
        ids = self.selected_ids()
        if self.mode.get()!=MANUAL or len(ids)!=1 or self.rows[str(ids[0])]['status']!='uncertain':
            messagebox.showinfo('逐张核对', '请选择一张已添加、正在等待核对的图片；确认收藏及手机同步后，再继续下一张。', parent=app.root)
            return
        context = app.profile_context()
        remaining_batch = [i for i in self.batch_ids if i!=ids[0]] if (
            self.batch_context==context and ids[0] in self.batch_ids) else []
        app.store.resolve_many(ids, app.account.get().strip(), True)
        app.reload_rows()
        app.stop_event.clear()
        def resume():
            if app.profile_context()!=context or app.stop_event.is_set():
                return
            queued = {r['id'] for r in app.store.exception_rows(app.account.get().strip())
                if r['status']=='pending' and r['recovery_queued']}
            remaining = [i for i in remaining_batch if i in queued]
            if remaining:
                self.start_retry(batch_ids=remaining)
            else:
                app.status.set('本张已确认收藏；本批重试已结束。继续其他图片时，选中待重试项后点击重试按钮。')
        app.cleanup_confirmed(item_id=ids[0], on_done=resume)

    def close(self):
        self.images.close()

    def retry_action(self):
        # A selection must never fall back to unrelated queued items, even when
        # those selected rows are no longer eligible for retry.
        self.start_retry(all_queued=not bool(self.tree.selection()))
