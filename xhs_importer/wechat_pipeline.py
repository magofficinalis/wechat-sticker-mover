"""Join one source snapshot to the existing exact-content queue, without broad batches."""
from dataclasses import dataclass, field
import os
import time
from pathlib import Path

from .catalog import inspect_file, file_digest, ImageItem
from .store import Store
from .wechat_reader import check_stop, read_stickers
from .wechat_export import export_stickers
from .engine import ImportEngine


@dataclass
class BatchResult:
    eligible_ids: list = field(default_factory=list)
    unverified_ids: list = field(default_factory=list)
    source_ids: list = field(default_factory=list)
    added: int = 0
    duplicate: int = 0
    invalid: int = 0


def preflight_import(settings, profile, store, device, stop):
    check_stop(stop)
    identity = device.identity(profile.package)
    if identity['serial'] != settings.serial or identity['version'] != profile.version:
        raise ValueError('模拟器或小红书版本与当前配置不一致，请重新确认。')
    context = profile.verification_context(settings.account, settings.serial, settings.adb)
    profile.validate(auto=True, context=context)
    if profile.verifier:
        verified = store.verified_formats(settings.account, context, require_visual=True)
        if not set(profile.tested_formats).issubset(verified):
            raise ValueError('请先完成当前账号和设备下的格式试导。')
    if any(r['status'] in {'uncertain', 'running', 'submitting'} for r in store.rows(settings.account)):
        raise ValueError('有待核对的历史任务，请先到“图片与任务”核对或跳过。')
    engine = ImportEngine(store, device, profile, settings.account, lambda *_: None, stop, settings.adb)
    engine.wait_selector(profile.favorites, profile.package)
    check_stop(stop)


def queue_export(store, account, paths, tested_formats, stop, notify=lambda _: None):
    items = []
    known = {row['digest']: row for row in store.rows(account)}
    for index, path in enumerate(paths, 1):
        check_stop(stop)
        path = Path(path)
        previous = None
        try:
            digest = file_digest(path)
            previous = known.get(digest)
            if previous and previous['size'] != path.stat().st_size:
                previous = None
        except OSError:
            pass
        if previous:
            items.append(ImageItem(path, digest, previous['format'], previous['width'],
                previous['height'], previous['frames'], previous['size']))
        else:
            items.append(inspect_file(path, lambda: check_stop(stop)))
        if index % 20 == 0 or index == len(paths):
            notify(f'核对本地图片 {index}/{len(paths)}；内容未变的历史图片复用校验结果。')
    check_stop(stop)
    added, duplicate, invalid = store.add(account, items)
    hashes = {item.digest for item in items if not item.error}
    current = [row for row in store.rows(account) if row['digest'] in hashes]
    result = BatchResult(added=added, duplicate=duplicate, invalid=invalid,
                         source_ids=[r['id'] for r in current])
    for row in current:
        if row['status'] != 'pending':
            continue
        (result.eligible_ids if row['format'] in tested_formats else result.unverified_ids).append(row['id'])
    return result


def run_wechat_pipeline(source, data_dir, account, tested_formats, stop, notify, *,
                        read_fn=read_stickers, export_fn=export_stickers, import_fn=None):
    check_stop(stop)
    data_dir = Path(data_dir)
    started = time.monotonic()
    rows = read_fn(Path(source), stop, notify)
    notify(f'微信记录读取完成：{len(rows)} 条，耗时 {time.monotonic()-started:.1f} 秒；正在准备原图。')
    check_stop(stop)
    source_id = os.path.normcase(str(Path(source).resolve()))
    exported = export_fn(rows, source_id, data_dir / 'wechat-cache', stop, notify)
    check_stop(stop)
    notify('正在校验导出图片并核对已有收藏记录…')
    store = Store(data_dir / 'queue.sqlite3')
    try:
        batch = queue_export(store, account, exported.paths, tested_formats, stop, notify)
    finally:
        store.close()
    check_stop(stop)
    if import_fn and batch.eligible_ids:
        notify(f'开始导入本次微信的 {len(batch.eligible_ids)} 张待处理表情…')
        import_fn(batch.eligible_ids)
    return exported, batch
