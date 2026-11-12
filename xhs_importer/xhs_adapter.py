"""Guarded roles derived from observed Xiaohongshu 9.33.4 UI snapshots."""
from pathlib import Path
import math
import xml.etree.ElementTree as ET
from PIL import Image, ImageOps

PACKAGE = 'com.xingin.xhs'
VERSION = '9.33.4'
GRID = 'androidx.recyclerview.widget.RecyclerView'


def validate_role(selector):
    role = selector.get('xhs')
    keys = {'xhs', 'album'} if role == 'album-image' else {'xhs'}
    if role not in {'favorites-add', 'album-menu', 'album-image'} or set(selector) != keys:
        raise ValueError('未知或不完整的小红书内置定位。')
    if role == 'album-image' and (not isinstance(selector['album'], str) or not selector['album'].strip()):
        raise ValueError('选图必须指定专用相册。')


def resolve_role(xml, selector):
    from .profile import bounds
    root = ET.fromstring(xml)
    parents = {child: parent for parent in root.iter() for child in parent}
    nodes = [n for n in root.iter('node') if n.get('package') == PACKAGE
             and n.get('visible-to-user', 'true') != 'false' and bounds(n.attrib)]
    role = selector['xhs']
    if role == 'favorites-add':
        markers = [n for n in nodes if n.get('text') == '已添加的表情']
        if len(markers) != 1:
            return []
        header = parents.get(markers[0])
        grid = parents.get(header)
        if grid is None or grid.get('class') != GRID:
            return []
        children = list(grid)
        if len(children) < 2 or children[0] is not header:
            return []
        tile = children[1]
        icons = list(tile)
        if (tile.get('class') != 'android.widget.LinearLayout' or tile.get('clickable') != 'true'
                or len(icons) != 1 or icons[0] not in nodes
                or icons[0].get('class') != 'android.widget.ImageView'
                or icons[0].get('text') or icons[0].get('content-desc')):
            return []
        # This structure alone cannot distinguish a sticker from the + tile.
        # AndroidDevice.click additionally verifies the observed icon image.
        return [dict(icons[0].attrib)]
    if len([n for n in nodes if n.get('content-desc') == '关闭']) != 1:
        return []
    grids = [n for n in nodes if n.get('class') == GRID
             and not any(child is not n and child.get('class') == GRID for child in n.iter('node'))]
    if len(grids) != 1:
        return []
    if role == 'album-menu':
        titles = [n for n in nodes if n.get('class') == 'android.widget.TextView'
                  and (n.get('text') == '全部' or n.get('text', '').startswith('XHSImport_'))]
        if len(titles) != 1:
            return []
        wrapper = parents.get(titles[0])
        if wrapper is None or wrapper.get('class') != 'android.widget.LinearLayout':
            return []
        title = parents.get(wrapper)
        if title is None or title.get('class') != 'android.widget.RelativeLayout' or title.get('clickable') != 'true':
            return []
        return [dict(title.attrib)]
    if len([n for n in nodes if n.get('text') == selector['album']]) != 1:
        return []
    return [dict(n.attrib) for n in grids[0].iter('node') if n in nodes
            and n.get('class') == 'android.widget.ImageView'
            and n.get('clickable') == 'true' and not n.get('content-desc')]


def _correlation(a, b):
    av, bv = a.tobytes(), b.tobytes()
    am, bm = sum(av) / len(av), sum(bv) / len(bv)
    covariance = sum((x-am)*(y-bm) for x,y in zip(av,bv))
    scale = math.sqrt(sum((x-am)**2 for x in av)*sum((y-bm)**2 for y in bv))
    return covariance / scale if scale else 0


def verify_add_icon(screenshot, node):
    from .profile import bounds
    area = bounds(node)
    if not area or area[2] > screenshot.width or area[3] > screenshot.height:
        raise ValueError('添加图标超出截图范围，已停止。')
    with Image.open(Path(__file__).parent / 'assets' / 'xhs-add-9.33.4.png') as source:
        reference = ImageOps.grayscale(source).resize((64,64), Image.Resampling.LANCZOS)
    actual = ImageOps.grayscale(screenshot.crop(area)).resize((64,64), Image.Resampling.LANCZOS)
    if (_correlation(actual, reference) < .94
            or _correlation(actual.crop((20,20,44,44)), reference.crop((20,20,44,44))) < .90):
        raise ValueError('未能确认“＋”添加图标，已停止，以免误点已有表情。')


def make_profile(version, visual=True):
    from .profile import Profile, FlowStep
    if version != VERSION:
        raise ValueError(f'内置配置适用于小红书 {VERSION}，当前版本 {version} 需重新适配。')
    def step(action, selector):
        return FlowStep(action, selector, package=PACKAGE)
    return Profile(package=PACKAGE, version=VERSION, verifier='xhs-9.33.4-visual-v1' if visual else '',
        favorites={'text': '已添加的表情', 'package': PACKAGE},
        album_marker={'text': '{album}', 'package': PACKAGE},
        open_steps=[step('click', {'xhs': 'favorites-add'})],
        pick_steps=[step('click', {'xhs': 'album-menu'}), step('click', {'text': '{album}', 'package': PACKAGE}),
                    step('target', {'xhs': 'album-image', 'album': '{album}'})],
        submit_steps=[step('wait', {'text': '表情预览', 'package': PACKAGE}),
                      step('click', {'text': '添加', 'package': PACKAGE}),
                      step('wait', {'text': '已添加的表情', 'package': PACKAGE})])
