import copy
import tkinter as tk
from types import SimpleNamespace
from unittest.mock import Mock
import pytest

from xhs_importer.calibration import Calibration
from xhs_importer.xhs_adapter import make_profile, PACKAGE


XML = f'''<hierarchy>
<node package="{PACKAGE}" text="" resource-id="same" class="Layout" bounds="[0,0][500,500]"/>
<node package="{PACKAGE}" text="添加" resource-id="same" class="Text" clickable="true" bounds="[0,0][20,20]"/>
<node package="{PACKAGE}" text="图片" resource-id="same" class="Image" clickable="true" bounds="[20,0][40,20]"/>
<node package="{PACKAGE}" text="图片" resource-id="same" class="Image" clickable="true" bounds="[40,0][60,20]"/>
</hierarchy>'''


def create_window(tmp_path):
    root=tk.Tk();root.withdraw()
    app=SimpleNamespace(root=root,profile=make_profile('9.33.4'),busy=False,
        calibration_window=None,data_dir=tmp_path,show_setup=Mock(),prepare_profile=Mock(),
        refresh_profile=Mock(),status=tk.StringVar())
    window=Calibration(app)
    app.calibration_window=window
    return app,window


def test_guide_first_and_search_does_not_modify_profile(tmp_path):
    app,window=create_window(tmp_path)
    before=copy.deepcopy(app.profile)
    try:
        assert window.pages.index(window.pages.select())==0
        window.xml,window.current_pkg=XML,PACKAGE
        window.render_nodes()
        assert len(window.nodes.get_children())==3
        window.search.set('添加')
        assert len(window.nodes.get_children())==1
        key=window.nodes.get_children()[0]
        window.nodes.selection_set(key);window.selected()
        assert window.selector()['text']=='添加'
        assert app.profile==before
        window.use_builtin()
        app.show_setup.assert_called_once_with(2)
        app.prepare_profile.assert_not_called()
        assert app.profile==before
    finally:
        app.root.destroy()


def test_recorder_rejects_nonunique_choice_and_checks_placeholders(tmp_path):
    app,window=create_window(tmp_path)
    try:
        window.xml,window.current_pkg=XML,PACKAGE
        window.vars['resource-id'].set('same')
        with pytest.raises(ValueError,match='找到 4 个'):
            window.selector()
        window.vars['resource-id'].set('')
        window.vars['text'].set('{filename}')
        window.context={'filename':'添加','album':'XHSImport_demo'}
        assert window.selector()['text']=='{filename}'
        window.current_pkg='other.app'
        with pytest.raises(ValueError,match='找到 0 个'):
            window.selector()
    finally:
        window.destroy();app.root.destroy()


def test_unchanged_save_keeps_qualification_and_invalid_draft_cannot_replace_it(tmp_path, monkeypatch):
    app,window=create_window(tmp_path)
    try:
        app.profile.tested=app.profile.phone_sync=True
        app.profile.tested_formats=['PNG']
        app.profile.tested_context='existing-qualified-context'
        window.destroy();window=Calibration(app)
        app.profile.save(tmp_path/'profile.json')
        original=(tmp_path/'profile.json').read_bytes()
        window.save()
        assert (tmp_path/'profile.json').read_bytes()==original
        assert app.profile.tested
        window=Calibration(app)
        window.reset_draft()
        errors=[]
        monkeypatch.setattr('xhs_importer.calibration.messagebox.showerror',lambda *a,**k:errors.append(a))
        window.save()
        assert errors and window.winfo_exists()
        assert (tmp_path/'profile.json').read_bytes()==original
        assert app.profile.tested
    finally:
        app.root.destroy()


def test_search_clears_old_selection_and_busy_blocks_recording(tmp_path):
    app,window=create_window(tmp_path)
    try:
        window.xml,window.current_pkg=XML,PACKAGE
        window.render_nodes()
        window.nodes.selection_set(window.nodes.get_children()[0]);window.selected()
        window.search.set('不存在')
        assert not window.nodes.selection()
        assert not any(v.get() for v in window.vars.values())
        before=copy.deepcopy(window.p)
        app.busy=True
        window.reset_draft();window.add_step();window.set_marker('favorites')
        assert window.p==before
    finally:
        window.destroy();app.root.destroy()


def test_system_photo_picker_is_recorded_with_observed_package(tmp_path):
    app,window=create_window(tmp_path)
    try:
        window.current_pkg='com.android.providers.media.module'
        window.xml=f'<hierarchy><node package="{window.current_pkg}" text="选择" bounds="[0,0][20,20]"/></hierarchy>'
        window.vars['text'].set('选择')
        assert window.selector()=={'text':'选择','package':window.current_pkg}
        window.phase.set('选择图片')
        window.add_step()
        assert window.p.pick_steps[-1].package==window.current_pkg
    finally:
        window.destroy();app.root.destroy()


@pytest.mark.parametrize('edited',[False,True])
def test_stale_window_cannot_reset_new_qualification(tmp_path, monkeypatch, edited):
    app,window=create_window(tmp_path)
    try:
        if edited:
            window.p.result={'text':'本次新增成功','package':PACKAGE}
            window.p.verifier=''
        app.profile.tested=app.profile.phone_sync=True
        app.profile.tested_formats=['PNG']
        app.profile.tested_context='just-qualified'
        app.profile.save(tmp_path/'profile.json')
        original=(tmp_path/'profile.json').read_bytes()
        errors=[]
        monkeypatch.setattr('xhs_importer.calibration.messagebox.showerror',lambda *a,**k:errors.append(a))
        window.save()
        assert (tmp_path/'profile.json').read_bytes()==original
        assert app.profile.tested_formats==['PNG']
        assert bool(errors)==edited
        assert bool(window.winfo_exists())==edited
    finally:
        app.root.destroy()
