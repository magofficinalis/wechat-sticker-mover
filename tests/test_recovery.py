from pathlib import Path
import threading
import pytest
from PIL import Image
from xhs_importer.device import AndroidDevice
from xhs_importer.xhs_adapter import make_profile,PACKAGE
from xhs_importer.profile import unique_node,bounds

FIX=Path(__file__).parent/'fixtures/xhs-9.33.4'


class UI:
    def __init__(self):self.state='preview';self.presses=[]
    def press(self,key):self.presses.append(key);self.state='picker'
    def screenshot(self):
        im=Image.new('RGB',(2560,1440),'white')
        node=unique_node((FIX/'favorites-panel.xml').read_text(encoding='utf-8'),{'xhs':'favorites-add'})
        box=bounds(node);icon=Image.open(FIX/'add-icon.png').resize((box[2]-box[0],box[3]-box[1]));im.paste(icon,box[:2]);return im


def device():
    d=AndroidDevice(None,'test');ui=UI();d._ui=ui;d.current_package=lambda:PACKAGE
    d.identity=lambda p:{'version':'9.33.4'}
    def hierarchy():
        if ui.state=='unknown':return '<hierarchy/>'
        return (FIX/({'preview':'preview.xml','picker':'album-picker.xml','favorites':'favorites-panel.xml'}[ui.state])).read_text(encoding='utf-8')
    d.hierarchy=hierarchy
    clicks=[]
    def click(selector,stop=None):clicks.append(selector);ui.state='favorites'
    d.click=click
    return d,ui,clicks


def test_recovery_only_backs_out_and_closes_picker():
    d,ui,clicks=device();d.recover_favorites(make_profile('9.33.4'),threading.Event())
    assert ui.presses==['back']
    assert clicks==[{'content-desc':'关闭','package':PACKAGE}]
    assert ui.state=='favorites'


def test_unknown_page_never_gets_navigation():
    d,ui,clicks=device();ui.state='unknown'
    with pytest.raises(RuntimeError):d.recover_favorites(make_profile('9.33.4'),threading.Event())
    assert not clicks and not ui.presses


def test_recovery_does_not_touch_app_after_stop():
    d,ui,clicks=device();stop=threading.Event();stop.set()
    with pytest.raises(RuntimeError):d.recover_favorites(make_profile('9.33.4'),stop)
    assert not clicks and not ui.presses


def test_pause_during_hierarchy_read_prevents_back_press():
    d,ui,clicks=device();stop=threading.Event();original=d.hierarchy
    def hierarchy():
        stop.set()
        return original()
    d.hierarchy=hierarchy
    with pytest.raises(RuntimeError):d.recover_favorites(make_profile('9.33.4'),stop)
    assert not clicks and not ui.presses


def test_short_album_dropdown_can_safely_return_to_favorites():
    d,ui,clicks=device();ui.state='menu'
    original=d.hierarchy
    d.hierarchy=lambda:(FIX/'short-album-menu.xml').read_text(encoding='utf-8') if ui.state=='menu' else original()
    d.recover_favorites(make_profile('9.33.4'),threading.Event())
    assert ui.state=='favorites'
    assert ui.presses==['back']
    assert clicks==[{'content-desc':'关闭','package':PACKAGE}]
