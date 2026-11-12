"""Read a selected Weixin emoticon snapshot; secrets and plaintext stay in memory."""
import hashlib
import hmac
import os
from pathlib import Path
import sqlite3
import struct
import subprocess
import time

from .engine import Cancelled
from ._vendor.wechat_meme_export.decrypt import decrypt_page, verify_first_page

PAGE = 4096
MAX_DATABASE = 64 * 1024 * 1024
COLUMNS = 'md5,caption,product_id,aes_key,cdn_url,extern_url,encrypt_url,thumb_url'


def check_stop(stop):
    if stop.is_set():
        raise Cancelled('已暂停；已完成的表情缓存会保留。')


def discover_sources(saved=None, roots=None):
    if roots is None:
        home = Path(os.environ.get('USERPROFILE', Path.home()))
        roots = [home / 'Documents/xwechat_files']
        for name in ('OneDrive', 'OneDriveConsumer'):
            if os.environ.get(name):
                roots.append(Path(os.environ[name]) / 'Documents/xwechat_files')
        if os.name == 'nt':
            try:
                import winreg
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                        r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders') as key:
                    personal = os.path.expandvars(winreg.QueryValueEx(key, 'Personal')[0])
                    roots.append(Path(personal) / 'xwechat_files')
            except OSError:
                pass
    found = set()
    if saved and Path(saved).name.lower() == 'emoticon.db' and Path(saved).is_file():
        found.add(Path(saved).resolve())
    for root in roots:
        try:
            found.update(p.resolve() for p in Path(root).glob('*/db_storage/emoticon/emoticon.db') if p.is_file())
        except OSError:
            continue
    return sorted(found, key=lambda p: str(p).casefold())


def _bounded_read(path, stop, optional=False):
    check_stop(stop)
    try:
        with path.open('rb') as stream:
            data = stream.read(MAX_DATABASE + 1)
    except FileNotFoundError:
        if optional:
            return b''
        raise
    if len(data) > MAX_DATABASE:
        raise ValueError('微信表情数据库或日志超过 64 MB，暂不自动读取。')
    check_stop(stop)
    return data


def stable_snapshot(path, stop):
    path = Path(path)
    wal = path.with_name(path.name + '-wal')
    journal = path.with_name(path.name + '-journal')
    if journal.exists() and journal.stat().st_size:
        with journal.open('rb') as stream:
            if stream.read(8) == bytes.fromhex('d9d505f920a163d7'):
                raise ValueError('微信数据库正在写入，请稍后重试。')
    for _ in range(3):
        first = (_bounded_read(path, stop), _bounded_read(wal, stop, True))
        if stop.wait(.08):
            check_stop(stop)
        second = (_bounded_read(path, stop), _bounded_read(wal, stop, True))
        if first == second:
            return second
    raise ValueError('微信表情数据正在变化，请稍后重试。')


def _checksum(data, state, endian):
    a, b = state
    for x, y in struct.iter_unpack(endian + 'II', data):
        a = (a + x + b) & 0xffffffff
        b = (b + y + a) & 0xffffffff
    return a, b


def _committed_wal(wal, stop):
    if not wal:
        return {}, None
    if len(wal) < 32:
        raise ValueError('WAL 日志头不完整。')
    magic, version, size, _, salt1, salt2, c1, c2 = struct.unpack('>8I', wal[:32])
    if magic not in (0x377f0682, 0x377f0683) or version != 3007000 or size != PAGE:
        raise ValueError('WAL 日志格式不受支持。')
    endian = '<' if magic == 0x377f0682 else '>'
    checksum = _checksum(wal[:24], (0, 0), endian)
    if checksum != (c1, c2):
        raise ValueError('WAL 日志头校验失败。')
    pending, committed, committed_size = {}, {}, None
    for offset in range(32, len(wal) - 24 - PAGE + 1, 24 + PAGE):
        check_stop(stop)
        frame = wal[offset:offset + 24 + PAGE]
        number, db_size, s1, s2, c1, c2 = struct.unpack('>6I', frame[:24])
        if (s1, s2) != (salt1, salt2):
            break  # Recycled tail from an earlier WAL generation.
        if not 0 < number <= MAX_DATABASE // PAGE or db_size > MAX_DATABASE // PAGE:
            raise ValueError('WAL 页号或数据库大小无效。')
        checksum = _checksum(frame[:8] + frame[24:], checksum, endian)
        if checksum != (c1, c2):
            raise ValueError('WAL 帧校验失败，请稍后重试。')
        pending[number] = frame[24:]
        if db_size:
            committed.update(pending)
            committed = {n: p for n, p in committed.items() if n <= db_size}
            pending.clear()
            committed_size = db_size
    return committed, committed_size


def decode_snapshot(main, wal, key, stop):
    check_stop(stop)
    if not main or len(main) % PAGE or len(main) > MAX_DATABASE or len(wal) > MAX_DATABASE:
        raise ValueError('微信表情数据库大小无效。')
    if not verify_first_page(main[:PAGE], key):
        raise ValueError('微信表情数据库密钥校验失败。')
    replacements, pages = _committed_wal(wal, stop)
    pages = pages if pages is not None else len(main) // PAGE
    salt = main[:16]
    mac_key = hashlib.pbkdf2_hmac('sha512', key, bytes(v ^ 0x3a for v in salt), 2, dklen=32)
    output = bytearray()
    for number in range(1, pages + 1):
        check_stop(stop)
        page = replacements.get(number, main[(number - 1) * PAGE:number * PAGE])
        if len(page) != PAGE:
            raise ValueError('微信表情数据库缺少页面。')
        if page == bytes(PAGE) and number != 1:
            output.extend(page)
            continue
        start = 16 if number == 1 else 0
        if number == 1 and page[:16] != salt:
            raise ValueError('微信表情数据库来源校验失败。')
        mac = hmac.new(mac_key, page[start:-64] + number.to_bytes(4, 'little'), hashlib.sha512).digest()
        if not hmac.compare_digest(mac, page[-64:]):
            raise ValueError('微信表情数据库页面校验失败。')
        output.extend(decrypt_page(page, number - 1, key))
    # The merged memory image has no external WAL: deserialize as a standalone DB.
    output[18:20] = b'\1\1'
    output[28:32] = pages.to_bytes(4, 'big')
    return bytes(output)


def rows_from_plain(data):
    con = sqlite3.connect(':memory:')
    try:
        con.deserialize(data)
        con.execute('PRAGMA trusted_schema=OFF')
        con.execute('PRAGMA query_only=ON')
        if con.execute('PRAGMA integrity_check').fetchone() != ('ok',):
            raise ValueError('表情数据库完整性检查失败。')
        found = con.execute("SELECT type,sql FROM sqlite_schema WHERE name='kNonStoreEmoticonTable'").fetchone()
        if not found or found[0] != 'table' or 'VIRTUAL' in (found[1] or '').upper():
            raise ValueError('未找到支持的微信已添加表情数据表。')
        con.row_factory = sqlite3.Row
        rows = con.execute(f'SELECT {COLUMNS} FROM kNonStoreEmoticonTable ORDER BY md5 LIMIT 10001').fetchall()
        if len(rows) > 10000:
            raise ValueError('表情数量超过本版本单次读取上限。')
        return [{k: (v.decode('utf-8') if isinstance(v, bytes) else str(v or '')) for k, v in dict(row).items()} for row in rows]
    except sqlite3.Error as exc:
        raise ValueError('无法读取微信表情数据，请检查版本和登录状态。') from exc
    finally:
        con.close()


def _read_key(first_page, stop):
    from ._vendor.wechat_meme_export.memory import ProcessMemoryReader, ProcessScan
    from ._vendor.wechat_meme_export.memory import PROCESS_QUERY_INFORMATION, PROCESS_VM_READ
    deadline = time.monotonic() + 30
    class Reader(ProcessMemoryReader):
        def read(self, handle, address, size):
            result = bytearray()
            for offset in range(0, size, 1024 * 1024):
                check_stop(stop)
                if time.monotonic() > deadline:
                    raise TimeoutError('读取微信表情超时，请确认微信已登录后重试。')
                count = min(1024 * 1024, size - offset)
                part = super().read(handle, address + offset, count)
                result.extend(part)
                if len(part) < count:
                    break
            return bytes(result)
    reader = Reader()
    process = subprocess.run(['powershell', '-NoProfile', '-Command',
        'Get-Process Weixin -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id'],
        capture_output=True, text=True, timeout=10, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    pids = [int(line) for line in process.stdout.splitlines() if line.strip().isdigit()]
    if not pids:
        raise ValueError('请先启动并登录电脑版微信 4.x。')
    opened = False
    for pid in pids:
        check_stop(stop)
        if time.monotonic() > deadline:
            break
        handle = reader.open_process(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
        if not handle:
            continue
        opened = True
        try:
            result = ProcessScan()
            # Deliberately omit upstream's unbounded generic memory scan fallback.
            reader.scan_wcdb_config_key(pid, handle, first_page, deadline, set(), result)
            if len(result.keys) == 1:
                return bytes.fromhex(next(iter(result.keys)))
        finally:
            reader.close_handle(handle)
    if not opened:
        raise ValueError('无法读取微信进程，请以与微信相同的权限运行工具。')
    raise ValueError('未能读取所选微信的表情。请确认来源属于当前登录账号；支持微信 4.1.13.12 / 4.1.13.65 / 4.1.15.13。')


def read_stickers(path, stop, notify):
    check_stop(stop)
    path = Path(path)
    if path.name.lower() != 'emoticon.db':
        raise ValueError('请选择微信 db_storage/emoticon/emoticon.db。')
    notify('正在读取所选微信的表情数据…')
    main, wal = stable_snapshot(path, stop)
    key = _read_key(main[:PAGE], stop)
    check_stop(stop)
    current, log = stable_snapshot(path, stop)
    if current[:16] != main[:16]:
        raise ValueError('微信数据来源发生变化，请重新选择。')
    notify('正在校验最新表情记录…')
    return rows_from_plain(decode_snapshot(current, log, key, stop))
