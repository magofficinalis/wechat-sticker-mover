from dataclasses import dataclass, field, asdict
import hashlib
import json
import os
from pathlib import Path
import re
import uuid
import xml.etree.ElementTree as ET

ATTRS = {'text', 'resource-id', 'content-desc', 'class', 'package'}


def bounds(node):
    values = re.findall(r'-?\d+', node.get('bounds', ''))
    if len(values) != 4:
        return None
    x1, y1, x2, y2 = map(int, values)
    if x2 <= x1 or y2 <= y1 or x1 < 0 or y1 < 0:
        return None
    return x1, y1, x2, y2


def validate_selector(selector):
    if isinstance(selector, dict) and 'xhs' in selector:
        from .xhs_adapter import validate_role
        validate_role(selector)
        return
    if not isinstance(selector, dict) or not selector or set(selector) - ATTRS:
        raise ValueError('定位规则必须包含文字、资源 ID、描述、类别或包名。')
    if not all(isinstance(v, str) and v.strip() for v in selector.values()):
        raise ValueError('定位规则不能含空值。')


def matching_nodes(xml, selector):
    validate_selector(selector)
    if 'xhs' in selector:
        from .xhs_adapter import resolve_role
        return resolve_role(xml, selector)
    return [dict(n.attrib) for n in ET.fromstring(xml).iter('node')
            if bounds(n.attrib) and n.get('visible-to-user', 'true') != 'false'
            and all(n.get(k) == v for k, v in selector.items())]


def unique_node(xml, selector):
    nodes = matching_nodes(xml, selector)
    if len(nodes) != 1:
        roles = {'favorites-add': '收藏表情的“＋”入口', 'album-menu': '相册选择下拉入口',
                 'album-image': '专用相册中的目标图片'}
        label = roles.get(selector.get('xhs')) or selector.get('text') or selector.get('content-desc') or selector.get('resource-id') or str(selector)
        raise ValueError(f'未能唯一定位「{label}」：找到 {len(nodes)} 个，要求 1 个。请检查当前页面、列表位置或等待界面加载。')
    return nodes[0]


def expand(selector, context):
    return {k: v.replace('{filename}', context['filename']).replace('{album}', context['album'])
            for k, v in selector.items()}


@dataclass
class FlowStep:
    action: str
    selector: dict
    value: str = ''
    package: str = ''


@dataclass
class Profile:
    package: str = ''
    version: str = ''
    favorites: dict = field(default_factory=dict)
    album_marker: dict = field(default_factory=dict)
    result: dict = field(default_factory=dict)
    open_steps: list[FlowStep] = field(default_factory=list)
    pick_steps: list[FlowStep] = field(default_factory=list)
    submit_steps: list[FlowStep] = field(default_factory=list)
    tested: bool = False
    tested_formats: list[str] = field(default_factory=list)
    phone_sync: bool = False
    tested_context: str = ''
    revision: str = field(default_factory=lambda: uuid.uuid4().hex)
    verifier: str = ''

    def invalidate_verification(self):
        self.tested, self.phone_sync, self.tested_formats = False, False, []
        self.tested_context = ''
        self.revision = uuid.uuid4().hex

    def verification_context(self, account, serial, adb=''):
        recipe = {name: asdict(self)[name] for name in (
            'package', 'version', 'favorites', 'album_marker', 'result',
            'open_steps', 'pick_steps', 'submit_steps', 'revision')}
        if self.verifier:
            recipe['verifier'] = self.verifier
        payload = [recipe, account.strip(), serial.strip(), os.path.normcase(os.path.abspath(adb)) if adb else '']
        return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()

    def validate(self, auto=False, context=None):
        if not self.package or not self.version:
            raise ValueError('请先读取模拟器内小红书的包名和版本。')
        if self.verifier:
            from .visual import POLICY
            from .xhs_adapter import make_profile
            if self.verifier != POLICY:
                raise ValueError('未知的自动核对方式。')
            reference = make_profile(self.version, visual=False)
            for name in ('package', 'favorites', 'album_marker', 'result', 'open_steps', 'pick_steps', 'submit_steps'):
                if getattr(self, name) != getattr(reference, name):
                    raise ValueError('视觉核对只支持原始内置流程；修改流程后请关闭视觉核对。')
        steps = self.open_steps + self.pick_steps + self.submit_steps
        selectors = [self.favorites, self.album_marker, self.result] + [s.selector for s in steps]
        if any('xhs' in selector for selector in selectors):
            from .xhs_adapter import PACKAGE, VERSION
            if self.package != PACKAGE or self.version != VERSION or any(s.package != PACKAGE for s in steps):
                raise ValueError('内置小红书定位只支持原定包名和版本 9.33.4，不能修改版本后复用。')
        validate_selector(self.favorites)
        for group in (self.open_steps, self.pick_steps, self.submit_steps):
            if not group:
                raise ValueError('打开、选图、确认三个阶段都需要配置步骤。')
            for step in group:
                if step.action not in {'click', 'text', 'wait', 'target'}:
                    raise ValueError('不支持此操作类型。')
                validate_selector(step.selector)
                if step.action == 'text' and 'xhs' in step.selector:
                    raise ValueError('小红书内置定位不能用于文字输入。')
                if not step.package:
                    raise ValueError('每个步骤必须记录前台应用包名。')
        target = [s for s in self.pick_steps if s.action == 'target']
        if len(target) != 1:
            raise ValueError('选图阶段需要且只能有一个“选中目标图片”步骤。')
        if any(s.action == 'target' for s in self.open_steps + self.submit_steps):
            raise ValueError('目标图片选择只能在选图阶段。')
        named = any('{filename}' in v for v in target[0].selector.values())
        if not named:
            if not self.album_marker or not any('{album}' in v for v in self.album_marker.values()):
                raise ValueError('必须按文件名选图，或验证包含 {album} 的专用相册。')
            validate_selector(self.album_marker)
        # No actions after target belong in the pre-submit phase: tapping an image
        # could itself commit on a different app version. Engine marks uncertainty early.
        if self.pick_steps[-1].action != 'target':
            raise ValueError('目标图片必须是选图阶段最后一步；后续操作放入确认阶段。')
        if auto:
            if not self.tested or not self.phone_sync or not self.tested_formats:
                raise ValueError('请先完成单张实测和手机同步核对，再启用自动模式。')
            if not context or context != self.tested_context:
                raise ValueError('账号、设备或界面配置已变化，请在当前环境重新实测。')
            if not self.verifier:
                validate_selector(self.result)
                if self.result == self.favorites:
                    raise ValueError('结果证据不能只用收藏页面入口，必须是新增结果。')

    def save(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(path)

    @classmethod
    def load(cls, path):
        raw = json.loads(Path(path).read_text(encoding='utf-8'))
        for name in ('open_steps', 'pick_steps', 'submit_steps'):
            raw[name] = [FlowStep(**s) for s in raw.get(name, [])]
        return cls(**raw)
