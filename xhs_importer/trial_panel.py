"""Guided four-format trial screen, with one explicit phone-sync confirmation."""
import copy
from pathlib import Path
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageTk

from .config import start_problem
from .trial_batch import FORMATS, TrialBatch, has_trial_proof


class TrialPanel:
    def __init__(self, app, parent):
        self.app=app
        self.session=None
        self.running=False
        self.load_error=''
        self.images={}
        try:
            self.session=TrialBatch.load(app.data_dir)
        except (OSError,ValueError) as exc:
            self.load_error=str(exc)
        self.frame=ttk.Frame(parent)
        self.frame.pack(fill='x',pady=8)
        ttk.Label(self.frame,text='自动准备测试图，四种格式一次验证',style='Section.TLabel').pack(anchor='w')
        ttk.Label(self.frame,text='会实际添加到小红书收藏；之后可自行删除。已验证格式不重复测试。GIF 和 WEBP 测试图中的圆点会移动。',wraplength=880).pack(anchor='w',pady=(6,10))
        actions=ttk.Frame(self.frame);actions.pack(fill='x',pady=(0,10))
        self.start_button=app.button(actions,'自动试导四种格式',self.start_trials,style='Accent.TButton')
        self.start_button.pack(side='left')
        self.review_button=app.button(actions,'处理选中测试图',self.review_selected)
        self.review_button.pack(side='left',padx=8)
        self.replace_button=app.button(actions,'换一张测试图',self.replace_selected)
        style=ttk.Style(app.root)
        style.configure('Trial.Treeview',rowheight=60)
        self.tree=ttk.Treeview(self.frame,columns=('format','state'),show='tree headings',height=4,
                               selectmode='browse',style='Trial.Treeview')
        self.tree.heading('#0',text='测试图');self.tree.column('#0',width=82,stretch=False)
        self.tree.heading('format',text='格式');self.tree.column('format',width=100,stretch=False)
        self.tree.heading('state',text='结果');self.tree.column('state',width=460)
        self.tree.pack(fill='x')
        self.tree.bind('<<TreeviewSelect>>',lambda _:self.refresh_actions())
        self.message=tk.StringVar(value=self.load_error or '把模拟器停在“已添加的表情”面板，点击下面的自动试导。')
        ttk.Label(self.frame,textvariable=self.message,wraplength=880).pack(anchor='w',pady=10)
        ttk.Label(self.frame,text='请在手机端逐张核对：测试图已收藏、已同步；GIF 和 WEBP 的圆点都在移动。全部符合后再确认。',wraplength=880).pack(anchor='w',pady=(14,6))
        self.confirm_button=app.button(self.frame,'已全部同步，开始搬运',self.confirm_and_start,style='Accent.TButton')
        self.confirm_button.pack(anchor='w')

    def active_session(self):
        s=self.session
        return s if s and s.account==self.app.account.get().strip() and s.context==self.app.profile_context() else None

    def qualified_formats(self):
        app=self.app
        if not hasattr(app,'store') or start_problem(app.current_settings(),app.profile,True,True):
            return set()
        verified=app.store.verified_formats(app.account.get().strip(),app.profile_context(),require_visual=bool(app.profile.verifier))
        return set(app.profile.tested_formats)&verified

    def refresh(self):
        if not hasattr(self.app,'store'):
            return
        session=self.active_session()
        try:
            rows=session.rows(self.app.store) if session else []
        except ValueError as exc:
            self.message.set(str(exc));rows=[]
        by_format={r['format']:r for r in rows}
        qualified=self.qualified_formats()
        for fmt in FORMATS:
            row=by_format.get(fmt)
            state='已验证，无需重复试导' if fmt in qualified else '待试导'
            image=''
            if row:
                if has_trial_proof(row,session.context):
                    state='已确认手机同步' if session.confirmed else '已试导，等待你核对手机同步'
                elif row['status']=='pending': state='待试导 / 可继续'
                elif row['status'] in {'running','submitting'}: state='正在试导…'
                elif row['status']=='success': state='已收藏，但缺少自动证据；选中后换一张测试图'
                else: state='需要处理：'+row['note']
                key=(session.token,row['id'])
                if key not in self.images:
                    try:
                        with Image.open(row['path']) as picture:
                            thumbnail=picture.convert('RGB');thumbnail.thumbnail((50,50))
                            self.images[key]=ImageTk.PhotoImage(thumbnail,master=self.app.root)
                    except (OSError,ValueError):
                        pass
                image=self.images.get(key,'')
            if self.tree.exists(fmt): self.tree.item(fmt,values=(fmt,state),image=image)
            else: self.tree.insert('', 'end',iid=fmt,values=(fmt,state),image=image)
        self.refresh_actions(rows,qualified)

    def refresh_actions(self,rows=None,qualified=None):
        app=self.app
        if not hasattr(app,'store'):
            return
        session=self.active_session()
        try:
            rows=rows if rows is not None else (session.rows(app.store) if session else [])
        except ValueError:
            rows=[]
        qualified=self.qualified_formats() if qualified is None else qualified
        can_confirm=bool(session and not session.confirmed and rows and all(has_trial_proof(r,session.context) for r in rows))
        self.confirm_button.configure(state='normal' if can_confirm and not app.busy else 'disabled')
        all_qualified=set(FORMATS)<=qualified
        self.start_button.configure(text='四种格式均已验证' if all_qualified else ('继续未完成试导' if session and not session.confirmed else '自动试导四种格式'),
                                    state='disabled' if app.busy or all_qualified or self.load_error else 'normal')
        selected=set(self.tree.selection())
        review=any(r['format'] in selected and r['status'] in {'failed','skipped','uncertain'} for r in rows)
        self.review_button.configure(state='normal' if review and not app.busy else 'disabled')
        replace=any(r['format'] in selected and r['status']=='success' and not has_trial_proof(r,session.context) for r in rows)
        if replace:
            self.replace_button.pack(side='left',padx=6)
        else:
            self.replace_button.pack_forget()
        self.replace_button.configure(state='normal' if replace and not app.busy else 'disabled')

    def start_trials(self):
        app=self.app
        if app.busy or self.load_error:
            return
        settings=app.current_settings()
        if not settings.account or not app.confirmed.get():
            self.message.set('先确认当前小红书目标账号。');app.show_setup(1);return
        if not settings.adb or not settings.serial:
            self.message.set('先连接并选择模拟器。');app.show_setup(0);return
        if app.calibration_window is not None:
            self.message.set('请先关闭界面适配窗口。');return
        try:
            app.profile.validate()
        except (ValueError,TypeError):
            self.message.set('正在检测小红书，准备好后自动试导。')
            def prepared():
                if app.current_settings()==settings and app.confirmed.get() and not app.stop_event.is_set():
                    self.start_trials()
            app.stop_event.clear()
            app.prepare_profile(on_ready=prepared)
            return
        if not app.profile.verifier:
            self.message.set('当前是自定义适配，请使用下方“手动单张试导”。连续试导需要内置视觉核对。');return
        context=app.profile_context()
        try:
            session=self.active_session()
            if session is None or session.confirmed:
                missing=[fmt for fmt in FORMATS if fmt not in self.qualified_formats()]
                if not missing:
                    self.message.set('四种格式均已验证，可直接在“开始搬运”使用。');return
                if any(r['status'] in {'uncertain','running','submitting'} for r in app.store.rows(settings.account)):
                    raise ValueError('有上次尚未核对的任务，请先到异常处理确认，避免重复添加。')
                self.session=TrialBatch.create(app.data_dir,app.store,settings.account,context,missing)
                session=self.session
                self.images.clear()
            session.rows(app.store)
            device=app.make_device()
            app.reload_rows()
            self.message.set('正在依次试导；请保持模拟器页面不动。完成后统一核对手机上的测试图。')
            self.running=True
            app.launch_import(settings,device,copy.deepcopy(app.profile),None,False,trial_batch_ids=session.item_ids)
        except (OSError,ValueError) as exc:
            self.running=False
            self.message.set(str(exc))
        self.refresh()

    def review_selected(self):
        if self.app.busy:
            return
        session=self.active_session()
        if not session or not self.tree.selection():
            return
        key=str(session.items.get(self.tree.selection()[0],''))
        panel=self.app.exception_panel
        panel.search.set('');panel.filter.set('全部异常')
        self.app.reload_rows()
        self.app.tabs.select(self.app.exception_tab)
        if key in panel.rows:
            panel.tree.selection_set(key);panel.tree.focus(key);panel.tree.see(key);panel.row_detail()
        self.message.set('在异常处理中核对该图：确认未收藏后加入重试，再返回这里“继续未完成试导”。')

    def confirm_and_start(self):
        app=self.app
        if app.busy:
            return
        session=self.active_session()
        if session is None or session.confirmed or not app.confirmed.get():
            self.message.set('没有当前环境可确认的新试导，请确认账号和设备。');return
        context=app.profile_context()
        source=app.wechat_panel.source.get()
        try:
            existing=self.qualified_formats()
            formats=app.store.confirm_trials(session.item_ids,session.account,context)
            profile=copy.deepcopy(app.profile)
            profile.tested=profile.phone_sync=True
            profile.tested_formats=sorted(existing|formats)
            profile.tested_context=context
            profile.save(app.data_dir/'profile.json')
            app.profile=profile
            session.confirmed=True
            try:
                session.save(app.data_dir)
            except OSError:
                session.confirmed=False
                raise
        except (OSError,ValueError) as exc:
            self.message.set(str(exc));self.refresh();return
        app.refresh_profile();app.reload_rows()
        self.message.set('已确认本轮测试。正在清理测试暂存图，随后开始搬运。')
        def resume():
            if (app.profile_context()!=context or not app.confirmed.get() or app.stop_event.is_set()
                    or app.wechat_panel.source.get()!=source):
                self.message.set('验证结果已保留；环境发生变化或任务已暂停，请自行点击开始搬运。')
                return
            if app.wechat_panel.source_ready():
                app.tabs.select(app.wechat_tab)
                app.wechat_panel.start(True)
            else:
                app.tabs.select(app.import_tab)
                app.start_product_import(False)
        app.cleanup_confirmed(on_done=resume)

    def replace_selected(self):
        if self.app.busy:
            return
        session=self.active_session()
        selected=self.tree.selection()
        if not session or not selected:
            return
        try:
            session.replace_collected(self.app.data_dir,self.app.store,selected[0])
            self.app.reload_rows()
            self.message.set('已换成新的测试图，原收藏和记录保留。点击“继续未完成试导”只处理尚未完成的测试。')
        except (ValueError,OSError) as exc:
            self.message.set(str(exc))
        self.refresh()
