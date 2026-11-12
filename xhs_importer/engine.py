from pathlib import Path
import time

from .catalog import file_digest
from .profile import expand, matching_nodes, unique_node
from .store import can_retry_unsubmitted
from .visual import ManualVerificationRequired


class Cancelled(Exception):
    pass


class ImportEngine:
    visual_timeout = 12
    def __init__(self, store, device, profile, account, notify, stop_event, connection=''):
        self.store, self.device, self.profile = store, device, profile
        self.account, self.notify, self.stop = account, notify, stop_event
        self.connection = connection

    def check_stop(self):
        if self.stop.is_set():
            raise Cancelled('已暂停；请核对当前页面，再继续。')

    def cleanup_staging(self, **kwargs):
        try:
            self.device.cleanup_staging(self.store, self.account, self.stop,
                lambda text: self.notify('cleanup', text), **kwargs)
        except Exception as exc:
            # Cleanup is separate from collection; never reset a confirmed result.
            self.notify('cleanup', f'暂存图清理未完成，收藏结果保留：{exc}')

    def start_page_ready(self):
        try:
            self.wait_selector(self.profile.favorites, self.profile.package, timeout=2)
            self.check_stop()
            return True
        except Cancelled:
            self.notify('blocked', '任务已暂停，尚未开始下一张；任务状态保持原样。')
        except Exception as exc:
            self.notify('blocked', '尚未开始导入：请打开小红书“已添加的表情”收藏面板，再点导入。'
                '任务状态保持原样。\n检查结果：' + str(exc))
        return False

    def wait_selector(self, selector, package, timeout=10):
        deadline = time.monotonic() + timeout
        while True:
            self.check_stop()
            if self.device.current_package() != package:
                raise RuntimeError('前台应用发生变化，已停止操作。')
            xml = self.device.hierarchy()
            nodes = matching_nodes(xml, selector)
            if len(nodes) > 1:
                raise RuntimeError('界面元素不唯一，已停止操作，请重新适配。')
            if len(nodes) == 1:
                return nodes[0]
            if time.monotonic() >= deadline:
                label = selector.get('text') or selector.get('content-desc') or selector.get('xhs') or str(selector)
                raise RuntimeError(f'等待页面元素「{label}」超时；已停止，请核对当前页面。')
            if self.stop.wait(0.3):
                self.check_stop()

    def step(self, step, context):
        self.check_stop()
        selector = expand(step.selector, context)
        if step.action == 'click' and selector == {'text': context['album'], 'package': 'com.xingin.xhs'}:
            self.device.select_album(context['album'], self.stop)
            return
        self.wait_selector(selector, step.package)
        if step.action == 'target' and not any('{filename}' in v for v in step.selector.values()):
            # Both current album and sole candidate must be uniquely observable.
            unique_node(self.device.hierarchy(), expand(self.profile.album_marker, context))
            unique_node(self.device.hierarchy(), selector)
        if step.action in {'click', 'target'}:
            self.device.click(selector)
        elif step.action == 'text':
            value = step.value.replace('{filename}', context['filename']).replace('{album}', context['album'])
            self.device.set_text(selector, value)

    def run(self, limit=1, auto_verify=False, item_id=None, item_ids=None, skip_errors=False, *, trial_batch_ids=None):
        self.profile.validate()
        if limit is not None and (not isinstance(limit, int) or limit < 1):
            raise ValueError('处理数量必须为正整数。')
        identity = self.device.identity(self.profile.package)
        if identity['version'] != self.profile.version:
            raise ValueError('小红书版本已变化，请重新验证界面配置。')
        context_id = self.profile.verification_context(self.account, identity['serial'], self.connection)
        self.profile.validate(auto=auto_verify, context=context_id)
        if auto_verify and self.profile.verifier:
            verified = self.store.verified_formats(self.account, context_id, require_visual=True)
            if not set(self.profile.tested_formats).issubset(verified):
                raise ValueError('当前环境缺少通过视觉核对的单张试导，请先试导并确认结果。')
        rows = self.store.rows(self.account)
        reviewed=set()
        trial_rows=None
        if trial_batch_ids is not None:
            from .trial_batch import batch_rows, has_trial_proof
            if auto_verify or limit is not None or item_id is not None or item_ids is not None or skip_errors or not self.profile.verifier:
                raise ValueError('连续首次试导仅支持内置视觉适配，不能与普通自动导入混用。')
            trial_rows=batch_rows(rows,trial_batch_ids)
            reviewed={r['id'] for r in trial_rows if has_trial_proof(r,context_id)}
            if any(r['id'] not in reviewed and r['status']!='pending' and not can_retry_unsubmitted(r) for r in trial_rows):
                raise ValueError('本轮有结果不明的测试，请先到异常处理核对；确认未收藏后才能重试。')
        if any(r['status'] in {'uncertain', 'running', 'submitting'} and r['id'] not in reviewed for r in rows):
            raise ValueError('有上次尚未核对的任务，请先确认结果。')
        pending = ([r for r in trial_rows if r['id'] not in reviewed] if trial_rows is not None
                   else [r for r in rows if r['status'] == 'pending' and not r.get('setup_sample')])
        selectable = {r['id']: r for r in rows if not r.get('setup_sample') and (r['status'] == 'pending' or can_retry_unsubmitted(r))}
        if item_ids is not None:
            if item_id is not None or not item_ids or len(set(item_ids)) != len(item_ids):
                raise ValueError('请选择一组不重复的待处理任务。')
            if any(i not in selectable for i in item_ids):
                raise ValueError('所选包含不能直接导入的任务。请选择当前账号的待处理图片，'
                    '或有未提交记录的失败图片；已收藏、已跳过及可能已提交的图片请先核对。')
            pending = [selectable[i] for i in item_ids]
        if item_id is not None:
            if limit != 1:
                raise ValueError('指定图片只支持单张试导。')
            pending = [selectable[item_id]] if item_id in selectable else []
            if not pending:
                raise ValueError('选中的图片不能直接重试；请选择待处理图片，或先核对收藏结果。')
        if limit is not None:
            pending = pending[:limit]
        if pending:
            if not self.start_page_ready():
                return
            self.cleanup_staging(discover=True)
        done = skipped = 0
        for row in pending:
            if self.stop.is_set():
                break
            # Check before creating an attempt or changing any selected failure.
            # Repeat after cleanup and between images: the user may change pages.
            if not self.start_page_ready():
                return
            if row['status'] == 'failed':
                self.store.retry_unsubmitted(row['id'], self.account)
            submitted = False
            self.store.begin_attempt(row['id'], context_id, 'auto' if auto_verify else 'trial',
                                     f"设备 {identity['serial']}；应用版本 {identity['version']}")
            self.notify('update', f"正在处理：{Path(row['path']).name}")
            try:
                source = Path(row['path'])
                if not source.is_file() or file_digest(source) != row['digest']:
                    raise RuntimeError('源文件已被移动或修改，请重新扫描。')
                if auto_verify and row['format'] not in self.profile.tested_formats:
                    raise RuntimeError('此图片格式尚未通过单张验证，请先试导。')
                visual = None
                manual_only = False
                if self.profile.verifier:
                    try:
                        visual = self.device.prepare_visual(source)
                    except ManualVerificationRequired:
                        if auto_verify:
                            raise
                        manual_only = True
                        self.notify('update', '本张无法自动核对，按逐张模式添加；完成后会暂停，请在手机端确认收藏结果。')
                self.check_stop()
                context = self.device.stage(row)
                if context.get('remote'):
                    self.store.record_staged(row['id'], identity['serial'], context['remote'])
                if auto_verify and not self.profile.verifier:
                    result_selector = expand(self.profile.result, context)
                    if matching_nodes(self.device.hierarchy(), result_selector):
                        raise RuntimeError('开始前已存在结果标记，无法证明本次新增，已停止。')
                for step in self.profile.open_steps + self.profile.pick_steps:
                    if step.action == 'target':
                        # Selecting a tile may submit immediately on some versions.
                        # Persist BEFORE the first possible externally visible side effect.
                        self.store.set_status(row['id'], 'submitting', '已进入选图/确认阶段，异常时需核对。')
                        submitted = True
                    self.step(step, context)
                for step in self.profile.submit_steps:
                    self.step(step, context)
                    if visual is not None and step.action == 'click':
                        # The visual adapter validates this fixed recipe: the
                        # confirmation click is the only submit-stage action.
                        arm = getattr(visual, 'submitted', None)
                        if arm:
                            arm()
                        capture = getattr(self.device, 'capture_visual_banner', None)
                        if capture:
                            capture(visual)
                if visual is not None:
                    remaining = getattr(visual, 'remaining_window', self.visual_timeout)
                    deadline = time.monotonic() + min(self.visual_timeout, remaining)
                    while True:
                        self.check_stop()
                        if self.device.current_package() != self.profile.package:
                            raise RuntimeError('前台应用发生变化，已停止核对。')
                        if time.monotonic() >= deadline:
                            raise RuntimeError(getattr(visual, 'timeout_note',
                                '未取得本次收藏证据，请到异常处理核对。'))
                        observed = self.device.verify_visual(visual)
                        self.check_stop()
                        if self.device.current_package() != self.profile.package:
                            raise RuntimeError('前台应用发生变化，已停止核对。')
                        if observed:
                            self.store.record_visual(row['id'])
                            break
                        if time.monotonic() >= deadline:
                            raise RuntimeError(getattr(visual, 'timeout_note',
                                '未取得本次收藏证据，请到异常处理核对。'))
                        self.stop.wait(.15)
                if not auto_verify:
                    note = ('已执行添加；本张无法自动核对，请在手机端检查收藏及动画，然后点击“核对：已收藏”或“核对：未收藏，重新排队”。'
                            if manual_only else '已执行添加，请到收藏库核对该图片，然后点击“已收藏”或“未收藏”。')
                    if visual is not None:
                        note += ' ' + getattr(visual, 'success_note', '')
                    self.store.set_status(row['id'], 'uncertain', note)
                    if trial_rows is not None and visual is not None:
                        done += 1
                        self.notify('trial-progress', f"已试导 {row['format']}，等待统一核对手机同步；继续下一种格式。")
                        continue
                    self.notify('needs-review', '已暂停，等待你核对本张收藏结果。')
                    return
                if visual is None:
                    self.wait_selector(result_selector, self.profile.package)
                    self.wait_selector(self.profile.favorites, self.profile.package)
                    xml = self.device.hierarchy()
                    unique_node(xml, result_selector)
                    unique_node(xml, self.profile.favorites)
                proof = getattr(visual, 'success_note', '') or '已观察新增结果及收藏页面。'
                self.store.set_status(row['id'], 'success', f"{proof} 版本 {identity['version']}；{row['format']}")
                done += 1
                self.cleanup_staging(item_id=row['id'])
                self.notify('update', f'已验证 {done} 张。')
            except Exception as exc:
                state = 'uncertain' if submitted else ('pending' if isinstance(exc, Cancelled) else 'failed')
                self.store.set_status(row['id'], state, str(exc))
                if auto_verify and skip_errors and not isinstance(exc, Cancelled) and not self.stop.is_set():
                    try:
                        self.device.recover_favorites(self.profile, self.stop)
                        self.check_stop()
                        if self.device.identity(self.profile.package)['version'] != self.profile.version:
                            raise RuntimeError('小红书版本已变化。')
                        if self.device.current_package() != self.profile.package:
                            raise RuntimeError('前台应用已变化。')
                        unique_node(self.device.hierarchy(), self.profile.favorites)
                        self.check_stop()
                        self.store.skip(row['id'], self.account, automatic=True)
                        skipped += 1
                        self.notify('update', f'已跳过 {Path(row["path"]).name}，继续下一张；可稍后核对。')
                        continue
                    except Exception as recovery_error:
                        note = f'{exc}；暂不能继续：{recovery_error}'
                        self.store.set_status(row['id'], 'uncertain' if submitted else 'failed', note)
                        self.notify('needs-review' if submitted else 'error', note)
                        return
                self.notify('needs-review' if submitted else 'error', str(exc))
                return
        if trial_rows is not None:
            self.notify('completed', '首次试导已结束，请在手机核对测试图及动画，再统一确认。' if not self.stop.is_set() else '试导已暂停，已完成的测试保留；继续时不会重复提交。')
        else:
            self.notify('completed', f'本次已验证 {done} 张，已跳过 {skipped} 张。' if not self.stop.is_set() else '任务已暂停。')
