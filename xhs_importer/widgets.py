"""Small shared Tk widgets with behavior suited to the importer lists."""
import tkinter as tk
from tkinter import ttk


class ScrollingPage(ttk.Frame):
    """Keep onboarding actions reachable when font scaling makes a page taller."""
    def __init__(self, master):
        super().__init__(master)
        self.rowconfigure(0,weight=1)
        self.columnconfigure(0,weight=1)
        self.canvas=tk.Canvas(self,highlightthickness=0,background='#f4f5f7',yscrollincrement=20)
        self.canvas.grid(row=0,column=0,sticky='nsew')
        bar=OverflowScrollbar(self,orient='vertical',command=self.canvas.yview)
        bar.grid(row=0,column=1,sticky='ns')
        self.canvas.configure(yscrollcommand=bar.set)
        self.body=ttk.Frame(self.canvas,padding=14)
        item=self.canvas.create_window((0,0),window=self.body,anchor='nw')
        self.body.bind('<Configure>',lambda _:self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>',lambda event:self.canvas.itemconfigure(item,width=event.width))
        self._windowing_system=self.tk.call('tk','windowingsystem')
        self._wheel_remainder=0
        # Tk does not bubble a child's wheel event through its parent Frames.
        # Observe this window, but act only on this page's canvas/descendants.
        self._wheel_target=self.winfo_toplevel()
        self._wheel_bindings={sequence:self._wheel_target.bind(sequence,self._on_wheel,add='+')
                              for sequence in ('<MouseWheel>','<Button-4>','<Button-5>')}
        self.bind('<Destroy>',self._unbind_wheel,add='+')

    def _on_wheel(self,event):
        if event.state & 1 or not self.winfo_viewable():
            return
        widget=event.widget
        while widget is not self.canvas:
            if widget is None:
                return
            # A list or text area with its own overflow keeps its native wheel.
            # The four fully visible trial cards instead pass it to the page.
            if hasattr(widget,'yview'):
                first,last=widget.yview()
                if last-first < 1-1e-9:
                    return
            if widget.winfo_class() in {'TCombobox','Spinbox','TSpinbox','Scale','TScale'}:
                return
            widget=getattr(widget,'master',None)
        first,last=self.canvas.yview()
        if last-first >= 1-1e-9:
            return
        if event.num in (4,5):
            units=-3 if event.num==4 else 3
        elif self._windowing_system=='aqua':
            units=-int(event.delta)*3
        else:
            self._wheel_remainder+=event.delta
            notches=int(self._wheel_remainder/120)
            self._wheel_remainder-=notches*120
            units=-notches*3
        if units:
            self.canvas.yview_scroll(units,'units')
        return 'break'

    def _unbind_wheel(self,event):
        if event.widget is self:
            for sequence,binding in self._wheel_bindings.items():
                # Older bundled Python/Tkinter unbind(funcid) also removes every
                # other handler for this sequence. Delete only our script line.
                script=self._wheel_target.bind(sequence)
                prefix=f'if {{"[{binding} '
                remaining='\n'.join(line for line in script.splitlines() if not line.startswith(prefix))
                self.tk.call('bind',str(self._wheel_target),sequence,remaining)
                self._wheel_target.deletecommand(binding)


class OverflowScrollbar(ttk.Scrollbar):
    """A grid-managed scrollbar that appears only for an actual scroll range."""

    def __init__(self, master=None, **kwargs):
        self._overflow = True
        self._grid_configured = False
        super().__init__(master, **kwargs)

    def grid_configure(self, *args, **kwargs):
        super().grid_configure(*args, **kwargs)
        self._grid_configured = True
        if not self._overflow:
            super().grid_remove()

    grid = grid_configure

    def set(self, first, last):
        overflow = float(last) - float(first) < 1.0 - 1e-9
        if self._grid_configured and overflow != self._overflow:
            if overflow:
                super().grid()
            else:
                super().grid_remove()
        self._overflow = overflow
        super().set(first, last)
