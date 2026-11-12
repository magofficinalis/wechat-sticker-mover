"""Guide-first setup help and an explicit, guarded advanced recipe editor."""
import copy
import tkinter as tk
from tkinter import ttk, messagebox
import xml.etree.ElementTree as ET
from .profile import FlowStep, Profile, bounds, expand, matching_nodes, validate_selector
from .calibration_help import CALIBRATION_HELP
from .help_view import GuideView
from .xhs_adapter import PACKAGE


class Calibration(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title('界面适配 · 操作说明与手动记录')
        self.geometry('1080x790')
        self.minsize(900, 680)
        self.opening_profile = copy.deepcopy(app.profile)
        self.p = copy.deepcopy(self.opening_profile)
        self.xml, self.current_pkg, self.context = '', '', {}
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.vars = {key: tk.StringVar() for key in ('text', 'resource-id', 'content-desc', 'class')}
        self.pages=ttk.Notebook(self)
        self.pages.pack(fill='both',expand=True,padx=12,pady=12)
        guide=ttk.Frame(self.pages,padding=12)
        box=ttk.Frame(self.pages,padding=12)
        self.pages.add(guide,text='先看这里：新电脑怎么用')
        self.pages.add(box,text='手动记录（高级）')
        ttk.Label(guide,text='先用自动配置；只有不兼容时才需要手动记录。',style='Section.TLabel').pack(anchor='w',pady=(0,10))
        ttk.Button(guide,text='去首次验证，使用自动配置',command=self.use_builtin).pack(anchor='w',pady=(0,10))
        self.guide_view=GuideView(guide,CALIBRATION_HELP)
        self.guide_view.pack(fill='both',expand=True)
        ttk.Label(box, text='先手动走通一张，再记录每一步', style='Section.TLabel').pack(anchor='w')
        ttk.Label(box, text='在模拟器中操作；每到一个页面点“读取界面”，选择元素并记录。手动演示的样例也需要回到主窗口核对结果。', wraplength=990).pack(anchor='w', pady=(5, 8))
        bar = ttk.Frame(box)
        bar.pack(fill='x')
        for text, command in [('读取当前应用版本', self.read_identity), ('暂存选中的任务图片', self.stage_sample),
                              ('读取界面', self.read_screen), ('查看截图', self.view_shot)]:
            ttk.Button(bar, text=text, command=command).pack(side='left', padx=(0, 8))
        self.info = tk.StringVar(value=f'应用：{self.p.package or "未读取"}  版本：{self.p.version or "—"}')
        ttk.Label(box, textvariable=self.info, wraplength=990).pack(anchor='w', pady=8)
        filters=ttk.Frame(box);filters.pack(fill='x',pady=(0,6))
        ttk.Label(filters,text='找按钮或文字：').pack(side='left')
        self.search=tk.StringVar()
        ttk.Entry(filters,textvariable=self.search,width=22).pack(side='left',padx=(0,8))
        self.useful_only=tk.BooleanVar(value=True)
        ttk.Checkbutton(filters,text='只看有文字或能点击的项',variable=self.useful_only,command=self.render_nodes).pack(side='left')
        self.advanced=tk.BooleanVar(value=False)
        ttk.Checkbutton(filters,text='高级定位字段',variable=self.advanced,command=self.toggle_advanced).pack(side='right')
        table=ttk.Frame(box);table.pack(fill='both',expand=True)
        table.columnconfigure(0,weight=1);table.rowconfigure(0,weight=1)
        self.nodes = ttk.Treeview(table, columns=('text', 'id', 'desc', 'class'), displaycolumns=('text','desc'), show='headings', height=5)
        for key, label, width in [('text', '文字', 220), ('id', '资源 ID', 340), ('desc', '描述', 200), ('class', '类型', 170)]:
            self.nodes.heading(key, text=label)
            self.nodes.column(key, width=width, stretch=True)
        scroll=ttk.Scrollbar(table,orient='vertical',command=self.nodes.yview)
        self.nodes.configure(yscrollcommand=scroll.set)
        self.nodes.grid(row=0,column=0,sticky='nsew');scroll.grid(row=0,column=1,sticky='ns')
        self.nodes.bind('<<TreeviewSelect>>', self.selected)
        self.search.trace_add('write',lambda *_:self.render_nodes())
        fields = ttk.Frame(box)
        self.fields=fields
        for idx, (key, label) in enumerate([('text', '文字'), ('resource-id', '资源 ID'), ('content-desc', '描述'), ('class', '类型')]):
            ttk.Label(fields, text=label).grid(row=idx // 2, column=(idx % 2) * 2, sticky='e', padx=5, pady=3)
            ttk.Entry(fields, textvariable=self.vars[key], width=42).grid(row=idx // 2, column=(idx % 2) * 2 + 1, sticky='ew')
        fields.columnconfigure(1, weight=1)
        fields.columnconfigure(3, weight=1)
        self.selection_note=tk.StringVar(value='选中一行后再记录；不知道对应哪个按钮时，请先看操作说明。')
        self.selection_label=ttk.Label(box,textvariable=self.selection_note,wraplength=990)
        self.selection_label.pack(anchor='w',pady=4)
        marks = ttk.Frame(box)
        marks.pack(fill='x', pady=6)
        for label, field in [('设为收藏页面标志', 'favorites'), ('设为专用相册标志', 'album_marker'), ('设为新增结果标志', 'result')]:
            ttk.Button(marks, text=label, command=lambda f=field: self.set_marker(f)).pack(side='left', padx=(0, 8))
        controls = ttk.Frame(box)
        controls.pack(fill='x', pady=6)
        self.phase = tk.StringVar(value='打开添加入口')
        self.action = tk.StringVar(value='点击')
        self.value = tk.StringVar()
        ttk.Combobox(controls, textvariable=self.phase, values=['打开添加入口', '选择图片', '确认添加'], state='readonly', width=15).pack(side='left')
        ttk.Combobox(controls, textvariable=self.action, values=['点击', '输入文字', '等待', '选中目标图片'], state='readonly', width=14).pack(side='left', padx=6)
        self.value_field=ttk.Entry(controls,textvariable=self.value,width=20)
        self.add_button=ttk.Button(controls, text='加入步骤', command=self.add_step)
        self.add_button.pack(side='left',padx=4)
        self.action.trace_add('write',lambda *_:self.toggle_value())
        ttk.Button(controls, text='删除选中步骤', command=self.remove_step).pack(side='left')
        ttk.Button(controls,text='清空录制草稿',command=self.reset_draft).pack(side='right')
        self.steps = tk.Listbox(box, height=6, font=('Microsoft YaHei UI', 9), borderwidth=1, relief='solid')
        self.steps.pack(fill='both', expand=True, pady=5)
        self.summary = tk.StringVar()
        ttk.Label(box, textvariable=self.summary, wraplength=990).pack(anchor='w', pady=5)
        foot = ttk.Frame(box)
        foot.pack(fill='x')
        ttk.Button(foot, text='保存配置并退出', command=self.save).pack(side='right')
        ttk.Label(foot, text='保存任何修改后，需重新完成单张验证。').pack(side='left')
        self.render_steps()

    def use_builtin(self):
        if self.app.busy:
            return
        self.close()
        self.app.show_setup(2)

    def toggle_value(self):
        if self.action.get()=='输入文字':
            self.value_field.pack(side='left',padx=6,before=self.add_button)
        else:
            self.value_field.pack_forget()

    def toggle_advanced(self):
        self.nodes.configure(displaycolumns=('text','id','desc','class') if self.advanced.get() else ('text','desc'))
        if self.advanced.get():
            self.fields.pack(fill='x',pady=6,before=self.selection_label)
        else:
            self.fields.pack_forget()

    def render_nodes(self):
        if not self.xml:
            return
        self.node_data=[]
        self.nodes.delete(*self.nodes.get_children())
        query=self.search.get().strip().casefold()
        for node in ET.fromstring(self.xml).iter('node'):
            if (not bounds(node.attrib) or node.get('visible-to-user','true')=='false'
                    or node.get('package')!=self.current_pkg):
                continue
            if self.useful_only.get() and not (node.get('text') or node.get('content-desc') or node.get('clickable')=='true'):
                continue
            values=[node.get(k,'') for k in ('text','resource-id','content-desc','class')]
            if query and not any(query in value.casefold() for value in values):
                continue
            idx=len(self.node_data)
            self.node_data.append(dict(node.attrib))
            self.nodes.insert('','end',iid=str(idx),values=values)
        # A search or page change invalidates the old selection.
        for var in self.vars.values():
            var.set('')
        self.selection_note.set(f'当前显示 {len(self.node_data)} 项。选中要记录的文字或按钮；找不到时可取消筛选或搜索。')

    def close(self):
        if self.app.busy:
            messagebox.showinfo('操作进行中', '请等待当前读取或暂存操作完成。', parent=self)
            return
        self.app.calibration_window = None
        self.destroy()

    def device(self):
        return self.app.make_device()

    def read_identity(self):
        if self.app.busy:
            return
        try:
            device = self.device()
        except Exception as exc:
            messagebox.showerror('连接', str(exc), parent=self)
            return
        def task():
            package = device.current_package()
            if package!=PACKAGE:
                raise ValueError('请先在模拟器打开小红书，再读取版本。')
            return device.identity(package)
        def done(info):
            self.p.package, self.p.version = info['package'], info['version']
            self.info.set(f'应用：{self.p.package}  版本：{self.p.version}。请确认这是小红书。')
        self.app.background(task, done)

    def stage_sample(self):
        if self.app.busy:
            return
        row = self.app.selected_row()
        if not row:
            messagebox.showinfo('选择素材', '请先在主窗口的任务列表中选中一张图片。', parent=self)
            return
        try:
            if not self.app.confirmed.get():
                raise ValueError('请先在主窗口确认模拟器当前登录的账号。')
            device = self.device()
            # Reserve before copying: the user may manually submit as soon as
            # the image is visible, even if staging later times out or we crash.
            self.app.store.reserve_calibration(row['id'], self.app.account.get().strip())
            self.app.reload_rows()
        except Exception as exc:
            messagebox.showerror('连接', str(exc), parent=self)
            return
        def done(context):
            self.context = context
            self.info.set(f'相册：{context["album"]}    文件：{context["filename"]}\n样例已设为待核对。完成手动演示后，关闭向导并核对是否已收藏。')
        self.app.background(lambda: device.stage(row), done)

    def read_screen(self):
        if self.app.busy:
            return
        try:
            device = self.device()
        except Exception as exc:
            messagebox.showerror('连接', str(exc), parent=self)
            return
        def task():
            xml = device.hierarchy()
            package = device.current_package()
            directory = self.app.data_dir / 'diagnostics'
            directory.mkdir(exist_ok=True)
            (directory / 'screen.xml').write_text(xml, encoding='utf-8')
            device.screenshot(directory / 'screen.png')
            return xml, package
        def done(result):
            self.xml, self.current_pkg = result
            self.render_nodes()
            self.info.set(f'已读取 {len(self.node_data)} 个元素，前台应用：{self.current_pkg}')
        self.app.background(task, done)

    def view_shot(self):
        path = self.app.data_dir / 'diagnostics' / 'screen.png'
        if path.exists():
            import os
            os.startfile(path)

    def selected(self, _=None):
        if not self.nodes.selection():
            return
        n = self.node_data[int(self.nodes.selection()[0])]
        # Start with ID + text/description. The user can remove changing fields.
        for key, var in self.vars.items():
            value = n.get(key, '') if key != 'class' else ''
            for token, actual in self.context.items():
                if token in {'filename', 'album'}:
                    value = value.replace(actual, '{' + token + '}')
            var.set(value)
        label=n.get('text') or n.get('content-desc') or '没有文字的控件'
        self.selection_note.set(f'已选：{label}。记录时会检查它是否只对应一个位置；需要修改时展开“高级定位字段”。')

    def selector(self):
        if not self.current_pkg or not self.xml:
            raise ValueError('请先读取当前页面，再选择要记录的按钮。')
        selector = {k: v.get().strip() for k, v in self.vars.items() if v.get().strip()}
        validate_selector(selector)
        selector['package']=self.current_pkg
        context={'filename':self.context.get('filename',''),'album':self.context.get('album','')}
        for token in ('filename','album'):
            if any('{'+token+'}' in value for value in selector.values()) and not context[token]:
                raise ValueError('请先暂存样例图片，再使用本次文件名或相册名。')
        count=len(matching_nodes(self.xml,expand(selector,context)))
        if count!=1:
            raise ValueError(f'找到 {count} 个位置，需要只找到 1 个。请重新读取正确页面，或展开“高级定位字段”缩小范围。')
        return selector

    def set_marker(self, field):
        if self.app.busy:
            return
        try:
            selector = self.selector()
            setattr(self.p, field, selector)
            self.p.verifier=''
            self.render_steps()
        except ValueError as exc:
            messagebox.showerror('定位配置', str(exc), parent=self)

    def add_step(self):
        if self.app.busy:
            return
        try:
            selector = self.selector()
            if not self.current_pkg:
                raise ValueError('请先读取当前界面。')
            action = {'点击': 'click', '输入文字': 'text', '等待': 'wait', '选中目标图片': 'target'}[self.action.get()]
            group = {'打开添加入口': self.p.open_steps, '选择图片': self.p.pick_steps, '确认添加': self.p.submit_steps}[self.phase.get()]
            group.append(FlowStep(action, selector, self.value.get(), self.current_pkg))
            self.p.verifier=''
            self.render_steps()
        except ValueError as exc:
            messagebox.showerror('步骤配置', str(exc), parent=self)

    def render_steps(self):
        self.steps.delete(0, 'end')
        self.step_refs = []
        for label, group in [('打开', self.p.open_steps), ('选图', self.p.pick_steps), ('确认', self.p.submit_steps)]:
            for i, step in enumerate(group):
                self.step_refs.append((group, i))
                action={'click':'点击','wait':'等待出现','target':'选中目标图片','text':'输入文字'}.get(step.action,step.action)
                target=step.selector.get('text') or step.selector.get('content-desc') or step.selector.get('resource-id') or step.selector.get('xhs') or '控件'
                target={'favorites-add':'收藏面板的＋','album-menu':'相册切换入口','album-image':'专用相册中的图片'}.get(target,target)
                target=target.replace('{album}','本次专用相册').replace('{filename}','本次文件名')
                self.steps.insert('end', f'{label} {i+1}：{action} → {target}  {step.value}')
        self.summary.set('已记录标志：' + '、'.join(label for label, value in [('收藏页', self.p.favorites), ('专用相册', self.p.album_marker), ('新增结果', self.p.result)] if value))

    def remove_step(self):
        if self.app.busy:
            return
        if self.steps.curselection():
            group, index = self.step_refs[self.steps.curselection()[0]]
            del group[index]
            self.p.verifier=''
            self.render_steps()

    def reset_draft(self):
        if self.app.busy:
            return
        self.p=Profile(package=self.p.package,version=self.p.version)
        self.render_steps()
        self.info.set('已清空本窗口的录制草稿。保存前不会修改原配置；请按说明重新记录三个阶段。')

    def save(self):
        if self.app.busy:
            return
        if self.p==self.opening_profile:
            self.close()
            return
        if self.app.profile!=self.opening_profile:
            messagebox.showerror('配置已在其他页面更新',
                '打开本窗口后，主窗口的配置或验证结果已变化。请关闭本窗口后重新打开再编辑，避免覆盖刚保存的结果。',parent=self)
            return
        candidate=copy.deepcopy(self.p)
        candidate.invalidate_verification()
        try:
            candidate.validate()
            candidate.save(self.app.data_dir / 'profile.json')
        except (ValueError,TypeError,OSError) as exc:
            messagebox.showerror('还不能保存',f'{exc}\n请按操作说明补齐步骤，原配置保持不变。',parent=self)
            return
        self.app.profile = candidate
        self.app.refresh_profile()
        self.app.status.set('配置已保存。请先核对手动演示样例，再用待处理图片试导当前配置。')
        self.close()
