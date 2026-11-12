"""Delete only owned, content-verified staging copies of successful imports."""
import re

OWNED = re.compile(r'/sdcard/DCIM/XHSImport_([0-9a-f]{12})/([0-9a-f]{20})_\1\.(png|jpe?g|gif|webp)')
MEDIA = 'content://media/external/images/media'


def owned_path(remote, digest=None):
    match = OWNED.fullmatch(remote)
    if not match or (digest is not None and
            (not re.fullmatch('[0-9a-f]{64}', digest) or match[2] != digest[:20])):
        raise ValueError('暂存图片路径与任务不匹配，保留文件。')
    return match


def _check(stop):
    if stop.is_set():
        raise RuntimeError('已暂停暂存图清理。')


def _media_rows(adb, serial, where):
    output = adb.shell(serial, ['content', 'query', '--uri', MEDIA,
        '--projection', '_id:_data', '--where', where])
    rows = []
    for line in output.splitlines():
        match = re.fullmatch(r'Row: \d+ _id=(\d+), _data=(.+)', line.strip())
        if match:
            rows.append((match[1], match[2]))
        elif line.strip() and line.strip() != 'No result found.':
            raise RuntimeError('无法确认媒体库查询结果，保留暂存图。')
    return rows


def _file_state(adb, serial, remote):
    return adb.shell(serial, ['sh', '-c',
        'if [ -L "$1" ]; then echo link; elif [ -f "$1" ]; then echo file; '
        'elif [ -e "$1" ]; then echo other; else echo absent; fi', 'staging-check', remote])


def remove_staged(adb, serial, remote, digest, stop):
    owned_path(remote, digest)
    _check(stop)
    base = adb.shell(serial, ['realpath', '/sdcard/DCIM'])
    if not re.fullmatch(r'/storage/emulated/\d+/DCIM', base):
        raise ValueError('模拟器图片目录不符合已验证范围，保留文件。')
    expected = base + remote[len('/sdcard/DCIM'):]
    parent = remote.rsplit('/', 1)[0]
    # Even an absent file's parent must not redirect to another directory.
    state = _file_state(adb, serial, remote)
    if state not in {'file', 'absent'}:
        raise ValueError('暂存路径不是普通文件，保留文件。')
    if state == 'file':
        if (adb.shell(serial, ['realpath', parent]) != expected.rsplit('/', 1)[0]
                or adb.shell(serial, ['realpath', remote]) != expected):
            raise ValueError('暂存路径发生重定向，保留文件。')
        actual = adb.shell(serial, ['sha256sum', remote]).split()
        if not actual or actual[0] != digest:
            raise ValueError('暂存图片内容已变化，保留文件。')
    where = f"_data IN ('{remote}','{expected}')"
    rows = _media_rows(adb, serial, where)
    if any(path not in {remote, expected} for _, path in rows):
        raise ValueError('媒体库返回了其他图片，保留文件。')
    for media_id, _ in rows:
        _check(stop)
        adb.shell(serial, ['content', 'delete', '--uri', MEDIA + '/' + media_id])
    _check(stop)
    # Some Android providers delete the underlying file, others only the index.
    if _file_state(adb, serial, remote) == 'file':
        adb.shell(serial, ['rm', '-f', remote])
    if _file_state(adb, serial, remote) != 'absent' or _media_rows(adb, serial, where):
        raise RuntimeError('暂存图或媒体库记录尚未清理完毕，下次继续。')
    _check(stop)
    try:
        # Empty-directory removal only; never recursive and never another file.
        adb.shell(serial, ['rmdir', parent])
    except RuntimeError:
        pass


def cleanup_successful(store, adb, serial, account, stop, notify, *, discover=False, item_id=None):
    if stop.is_set():
        return 0
    if discover:
        by_prefix = {}
        for row in store.rows(account):
            if row['status'] == 'success' and (item_id is None or row['id'] == item_id):
                by_prefix.setdefault(row['digest'][:20], []).append(row)
        if by_prefix:
            notify('正在查找旧版已收藏表情的暂存副本…')
            base = adb.shell(serial, ['realpath', '/sdcard/DCIM'])
            if not re.fullmatch(r'/storage/emulated/\d+/DCIM', base):
                raise ValueError('图片目录未验证，保留旧版暂存图。')
            for _, path in _media_rows(adb, serial, "_data LIKE '%/DCIM/XHSImport_%'"):
                _check(stop)
                remote = '/sdcard/DCIM' + path[len(base):] if path.startswith(base + '/') else path
                match = OWNED.fullmatch(remote)
                candidates = by_prefix.get(match[2], []) if match else []
                if len(candidates) == 1:
                    # Registration alone cannot delete: full remote SHA is checked below.
                    store.record_staged(candidates[0]['id'], serial, remote)
    candidates = store.cleanup_candidates(account, serial, item_id=item_id)
    cleaned = 0
    for index, entry in enumerate(candidates, 1):
        if stop.is_set():
            break
        notify(f'清理已收藏暂存图 {index}/{len(candidates)}；保留失败、跳过和待核对图片。')
        try:
            remove_staged(adb, serial, entry['remote'], entry['digest'], stop)
            store.finish_cleanup(serial, entry['remote'])
            cleaned += 1
        except (RuntimeError, ValueError, OSError) as exc:
            store.finish_cleanup(serial, entry['remote'], error=str(exc))
            notify('收藏结果保留；暂存图未清理：' + str(exc))
    if cleaned:
        notify(f'已清理 {cleaned} 张成功收藏的模拟器暂存图。')
    return cleaned
