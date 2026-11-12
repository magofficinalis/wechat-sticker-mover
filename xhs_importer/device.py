from dataclasses import dataclass
from pathlib import Path
import os
import re
import shlex
import subprocess
import time
import uuid

from .profile import unique_node, bounds


@dataclass(frozen=True)
class DeviceInfo:
    serial: str
    state: str
    detail: str = ''


def parse_devices(output):
    found = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] in {'device', 'offline', 'unauthorized', 'recovery', 'sideload'}:
            found.append(DeviceInfo(parts[0], parts[1], ' '.join(parts[2:])))
    return found


class Adb:
    def __init__(self, path):
        self.path = str(path)

    def run(self, args, timeout=25):
        try:
            result = subprocess.run([self.path, *args], capture_output=True,
                                    encoding='utf-8', errors='replace', timeout=timeout,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except FileNotFoundError as exc:
            raise RuntimeError('找不到 adb.exe，请选择模拟器安装目录中的 adb.exe。') from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError('模拟器响应超时，请检查是否已启动。') from exc
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout or 'ADB 操作失败').strip()[:700])
        return result.stdout.strip()

    def devices(self):
        return parse_devices(self.run(['devices', '-l']))

    def connect(self, endpoint):
        if not re.fullmatch(r'(127\.0\.0\.1|localhost):[0-9]{1,5}', endpoint):
            raise ValueError('只接受本机模拟器地址，例如 127.0.0.1:实际端口。')
        port = int(endpoint.rsplit(':', 1)[1])
        if not 1 <= port <= 65535:
            raise ValueError('端口必须在 1–65535 之间。')
        return self.run(['connect', endpoint])

    def shell(self, serial, args, timeout=25):
        return self.run(['-s', serial, 'shell', ' '.join(shlex.quote(str(v)) for v in args)], timeout)

    def push(self, serial, local, remote):
        return self.run(['-s', serial, 'push', str(local), remote], timeout=90)

    def stage(self, serial, path, digest, timeout=20):
        suffix = Path(path).suffix.lower()
        if suffix not in {'.png', '.jpg', '.jpeg', '.gif', '.webp'} or not re.fullmatch('[0-9a-f]{64}', digest):
            raise ValueError('无法暂存此文件。')
        # Unique, single-file album. Never clear another app's files or rely on recency.
        token = uuid.uuid4().hex[:12]
        album = 'XHSImport_' + token
        filename = digest[:20] + '_' + token + suffix
        remote = f'/sdcard/DCIM/{album}/{filename}'
        self.shell(serial, ['mkdir', '-p', f'/sdcard/DCIM/{album}'])
        self.push(serial, path, remote)
        self.shell(serial, ['am', 'broadcast', '-a', 'android.intent.action.MEDIA_SCANNER_SCAN_FILE',
                            '-d', 'file://' + remote])
        # File transfer and media-index visibility are different operations.
        query = f"_data LIKE '%/DCIM/{album}/{filename}'"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            output = self.shell(serial, ['content', 'query', '--uri', 'content://media/external/images/media',
                                         '--projection', '_id:_data', '--where', query])
            if f'/DCIM/{album}/{filename}' in output and re.search(r'\b_id=\d+', output):
                return {'album': album, 'filename': filename, 'remote': remote}
            time.sleep(0.5)
        raise RuntimeError('图片已复制，但模拟器媒体库尚未确认收录。请检查图库/媒体权限；未执行添加。')


class AndroidDevice:
    def __init__(self, adb, serial):
        self.adb, self.serial = adb, serial
        self._ui = None

    @property
    def ui(self):
        if self._ui is None:
            try:
                import uiautomator2 as u2
            except ImportError as exc:
                raise RuntimeError('缺少界面连接组件，请运行安装依赖.cmd。') from exc
            # The user-selected adb is also the server used by adbutils.
            os.environ['ADBUTILS_ADB_PATH'] = self.adb.path
            self._ui = u2.connect(self.serial)
        return self._ui

    def identity(self, package):
        info = self.ui.app_info(package)
        return {'serial': self.serial, 'package': package, 'version': info['versionName']}

    def current_package(self):
        return self.ui.app_current()['package']

    def hierarchy(self):
        return self.ui.dump_hierarchy(compressed=False)

    def screenshot(self, path):
        self.ui.screenshot(str(path))

    def click(self, selector, stop=None):
        node = unique_node(self.hierarchy(), selector)
        if selector.get('xhs') == 'favorites-add':
            from .xhs_adapter import verify_add_icon, PACKAGE
            if self.current_package() != PACKAGE:
                raise ValueError('前台应用已变化，停止点击。')
            verify_add_icon(self.ui.screenshot(), node)
            current = unique_node(self.hierarchy(), selector)
            if bounds(current) != bounds(node):
                raise ValueError('添加图标位置发生变化，请重新读取页面。')
        x1, y1, x2, y2 = bounds(node)
        if stop is not None and stop.is_set():
            raise RuntimeError('已暂停，不再点击页面。')
        self.ui.click((x1 + x2) // 2, (y1 + y2) // 2)

    def set_text(self, selector, value):
        unique_node(self.hierarchy(), selector)
        keys = {'resource-id': 'resourceId', 'content-desc': 'description', 'class': 'className', 'package': 'packageName', 'text': 'text'}
        target = self.ui(**{keys[k]: v for k, v in selector.items()})
        target.set_text(value)

    def stage(self, row):
        return self.adb.stage(self.serial, Path(row['path']), row['digest'])

    def cleanup_staging(self, store, account, stop, notify, **kwargs):
        from .staging_cleanup import cleanup_successful
        return cleanup_successful(store, self.adb, self.serial, account, stop, notify, **kwargs)

    def select_album(self, album, stop):
        from .album_navigation import select_album
        select_album(self, album, stop)

    def recover_favorites(self, profile, stop):
        from .xhs_adapter import PACKAGE, VERSION, verify_add_icon
        from .profile import matching_nodes
        from .album_navigation import menu_rows
        import xml.etree.ElementTree as ET
        if stop.is_set():
            raise RuntimeError('已暂停，不自动恢复页面。')
        if profile.package != PACKAGE or profile.version != VERSION or self.identity(PACKAGE)['version'] != VERSION:
            raise RuntimeError('当前应用版本不支持自动返回收藏页。')
        for _ in range(6):
            if stop.is_set():
                raise RuntimeError('已暂停，不自动恢复页面。')
            if self.current_package() != PACKAGE:
                raise RuntimeError('前台应用已变化，请手动回到收藏页。')
            xml = self.hierarchy()
            if stop.is_set():
                raise RuntimeError('已暂停，不自动恢复页面。')
            nodes = list(ET.fromstring(xml).iter('node'))
            if any(n.get('text') in {'登录','验证码','安全验证','允许','拒绝'} for n in nodes):
                raise RuntimeError('检测到登录或授权页面，已停止自动返回。')
            if len(matching_nodes(xml, profile.favorites)) == 1:
                icon = unique_node(xml, {'xhs':'favorites-add'})
                verify_add_icon(self.ui.screenshot(), icon)
                return
            if (len(matching_nodes(xml, {'text':'表情预览','package':PACKAGE})) == 1
                    and len(matching_nodes(xml, {'text':'添加','package':PACKAGE})) == 1):
                self.ui.press('back')
            elif (len(matching_nodes(xml, {'xhs':'album-menu'})) == 1
                    and len(matching_nodes(xml, {'content-desc':'关闭','package':PACKAGE})) == 1):
                self.click({'content-desc':'关闭','package':PACKAGE}, stop=stop)
            elif menu_rows(xml, '')[0] is not None and any(n.get('text','').startswith('XHSImport_') for n in nodes):
                self.ui.press('back')
            else:
                # A transition may temporarily have no recognizable nodes.
                # Unknown pages receive no taps or back presses.
                if stop.wait(.35):
                    raise RuntimeError('已暂停，不自动恢复页面。')
                continue
            if stop.wait(.35):
                raise RuntimeError('已暂停，不自动恢复页面。')
        raise RuntimeError('无法确认收藏起始页，请手动打开收藏面板后继续。')

    def visual_observation(self):
        from .visual import has_success_banner
        from .xhs_adapter import PACKAGE, GRID, verify_add_icon
        import xml.etree.ElementTree as ET
        if self.current_package() != PACKAGE:
            raise ValueError('当前不是小红书，停止视觉核对。')
        xml = self.hierarchy()
        icon = unique_node(xml, {'xhs': 'favorites-add'})
        screen = self.ui.screenshot()
        if abs(screen.width / screen.height - 16/9) > .005:
            raise ValueError('当前屏幕比例尚未验证，请保持已适配的横屏比例。')
        verify_add_icon(screen, icon)
        root = ET.fromstring(xml)
        parents = {c:p for p in root.iter() for c in p}
        markers = [n for n in root.iter('node') if n.get('text') == '已添加的表情' and n.get('package') == PACKAGE]
        grid = parents[parents[markers[0]]]
        if grid.get('class') != GRID:
            raise ValueError('收藏列表结构变化，停止核对。')
        children = list(grid)
        first = None
        if len(children) > 2:
            tile = children[2]
            icons = list(tile)
            if (tile.get('class') != 'android.widget.LinearLayout' or len(icons) != 1
                    or icons[0].get('class') != 'android.widget.ImageView'):
                raise ValueError('收藏首项结构变化，停止核对。')
            area = bounds(icons[0].attrib)
            if not area or area[2] > screen.width or area[3] > screen.height:
                raise ValueError('收藏首项超出屏幕，停止核对。')
            first = screen.crop(area)
        return has_success_banner(screen), first

    def prepare_visual(self, path):
        from .visual import SourceMatcher, VisualEvidence, ManualVerificationRequired
        manual_error = None
        try:
            evidence = VisualEvidence(SourceMatcher(path))
        except ManualVerificationRequired as exc:
            manual_error = exc
        # Even a manually verified image requires a known page and no stale toast.
        deadline = time.monotonic() + 6
        while True:
            banner, first = self.visual_observation()
            if not banner or time.monotonic() >= deadline:
                if manual_error is not None:
                    if banner:
                        raise ValueError('开始前成功提示尚未消失，请稍后重试。')
                    raise manual_error
                evidence.begin(banner, first)
                break
            time.sleep(.2)
        return evidence

    def verify_visual(self, evidence):
        return evidence.observe(*self.visual_observation())

    def capture_visual_banner(self, evidence):
        """Catch a short toast immediately after confirmation, before XML reads."""
        from .visual import has_success_banner
        from .xhs_adapter import PACKAGE
        if self.current_package() != PACKAGE:
            raise ValueError('当前不是小红书，停止视觉核对。')
        screen = self.ui.screenshot()
        if self.current_package() != PACKAGE:
            raise ValueError('前台应用发生变化，停止视觉核对。')
        evidence.observe_banner(has_success_banner(screen))
