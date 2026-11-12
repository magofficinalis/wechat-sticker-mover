from pathlib import Path
import pytest
from xhs_importer.device import AndroidDevice

PACKAGE='com.xingin.xhs'
ALBUM='XHSImport_target'


def menu(name=ALBUM):
    return f'''<hierarchy><node class="android.widget.ListView" package="{PACKAGE}" scrollable="true" bounds="[0,100][1440,2560]">
    <node class="android.widget.RelativeLayout" package="{PACKAGE}" clickable="true" bounds="[0,1700][1440,1840]">
    <node text="{name}" package="{PACKAGE}" bounds="[140,1750][480,1790]"/></node></node></hierarchy>'''


def picker(album):
    xml=(Path(__file__).parent/'fixtures/xhs-9.33.4/single-album.xml').read_text(encoding='utf-8')
    return xml.replace('XHSImport_108bb598befa',album)


class Stop:
    def __init__(self): self.delays=[];self.stopped=False
    def is_set(self):return self.stopped
    def wait(self,delay):self.delays.append(delay);return self.stopped


class Phone:
    def __init__(self,stop,wrong_once=False):self.stop=stop;self.screen='menu';self.clicks=[];self.wrong_once=wrong_once
    def window_size(self):return (1440,2560)
    def click(self,x,y):
        self.clicks.append((x,y))
        if self.screen!='menu':self.screen='menu';return
        assert sum(self.stop.delays)>=.6, 'Do not tap during dropdown animation'
        self.screen='wrong' if self.wrong_once else 'target';self.wrong_once=False
    def swipe(self,*args,**kwargs):self.screen='menu'


def device(wrong=False):
    stop=Stop();ui=Phone(stop,wrong);d=AndroidDevice(None,'test');d._ui=ui
    d.current_package=lambda:PACKAGE
    d.hierarchy=lambda:menu() if ui.screen=='menu' else picker(ALBUM if ui.screen=='target' else 'XHSImport_old')
    return d,ui,stop


def test_menu_settles_before_click_and_selected_album_is_verified():
    d,ui,stop=device();d.select_album(ALBUM,stop)
    assert ui.screen=='target'
    assert len(ui.clicks)==1


def test_wrong_album_is_reopened_and_never_clicked_as_image():
    d,ui,stop=device(True);d.select_album(ALBUM,stop)
    assert ui.screen=='target'
    assert len(ui.clicks)==3  # wrong album row, menu header, correct album row


def test_pause_prevents_album_click():
    d,ui,stop=device();stop.stopped=True
    with pytest.raises(RuntimeError):d.select_album(ALBUM,stop)
    assert not ui.clicks


def test_repeated_wrong_album_stops_without_selecting_image():
    d,ui,stop=device()
    click=ui.click
    def always_wrong(x,y):
        if ui.screen=='menu':ui.wrong_once=True
        click(x,y)
    ui.click=always_wrong
    with pytest.raises(RuntimeError,match='名称不匹配'):d.select_album(ALBUM,stop)
    assert ui.screen=='wrong'
    assert len(ui.clicks)==5


def test_album_below_visible_page_is_scrolled_into_view():
    d,ui,stop=device()
    state={'scrolled':False}
    original=d.hierarchy
    d.hierarchy=lambda:menu('XHSImport_other') if ui.screen=='menu' and not state['scrolled'] else original()
    def swipe(*args,**kwargs):state['scrolled']=True
    ui.swipe=swipe
    d.select_album(ALBUM,stop)
    assert state['scrolled'] and ui.screen=='target'


def test_observed_short_menu_is_recognized_without_scrollability():
    from xhs_importer.album_navigation import menu_rows
    xml=(Path(__file__).parent/'fixtures/xhs-9.33.4/short-album-menu.xml').read_text(encoding='utf-8')
    viewport,rows=menu_rows(xml,'XHSImport_000000000001')
    assert viewport == (0,136,1440,2560)
    assert len(rows)==1 and rows[0]


def test_short_menu_selects_target_without_reopening_header_or_swiping():
    d,ui,stop=device()
    original=d.hierarchy
    d.hierarchy=lambda: original().replace('scrollable="true"','scrollable="false"')
    ui.swipe=lambda *a,**kw: pytest.fail('Short menu must not swipe')
    d.click=lambda *a,**kw: pytest.fail('Menu is already open; never reopen its hidden header')
    d.select_album(ALBUM,stop)
    assert ui.screen=='target' and len(ui.clicks)==1


def test_short_menu_missing_target_reports_album_without_navigation():
    d,ui,stop=device()
    d.hierarchy=lambda:menu('XHSImport_other').replace('scrollable="true"','scrollable="false"')
    ui.swipe=lambda *a,**kw: pytest.fail('No scrolling when all albums are visible')
    d.click=lambda *a,**kw: pytest.fail('Do not click header behind an open menu')
    with pytest.raises(RuntimeError,match='未找到目标相册'):
        d.select_album(ALBUM,stop)
    assert not ui.clicks


def test_pause_during_last_menu_read_prevents_row_click():
    d,ui,stop=device()
    original=d.hierarchy
    reads=[]
    def hierarchy():
        reads.append(True)
        if len(reads)==3:stop.stopped=True
        return original()
    d.hierarchy=hierarchy
    with pytest.raises(RuntimeError,match='暂停'):
        d.select_album(ALBUM,stop)
    assert not ui.clicks
