"""First-use guidance; advanced controls stay out of daily import pages."""
from tkinter import ttk
from .widgets import ScrollingPage
from .usage_help import HELP_TEXT


class SetupPanel:
    def __init__(self, app, parent):
        self.app = app
        ttk.Label(parent, text='点击下方标签完成首次设置，之后在“开始搬运”使用。', style='Section.TLabel').pack(anchor='w', pady=(0,10))
        self.steps=ttk.Notebook(parent)
        self.steps.pack(fill='both',expand=True)
        self.connection_page,self.source_page,self.advanced_page,self.help_page=[ttk.Frame(self.steps,padding=14) for _ in range(4)]
        self.verification_scroller=ScrollingPage(self.steps)
        self.verification_page=self.verification_scroller.body
        for page,label in [(self.connection_page,'1 连接模拟器'),(self.source_page,'2 微信和账号'),
                           (self.verification_scroller,'3 首次验证'),(self.advanced_page,'高级选项'),(self.help_page,'使用说明')]:
            self.steps.add(page,text=label)
        self.steps.bind('<<NotebookTabChanged>>',lambda _:self.refresh())

    def refresh(self):
        app=self.app
        if hasattr(app,'trial_panel'):
            app.trial_panel.refresh()
        if hasattr(app,'trial_confirm_button'):
            row=app.current_trial()
            eligible=row is not None and row['status'] in {'uncertain','success'}
            app.trial_confirm_button.configure(state='normal' if eligible and not app.busy else 'disabled')
