from dataclasses import dataclass, asdict
import json
import os
from pathlib import Path


def default_data_dir():
    base = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData' / 'Local')))
    return base / 'XHSStickerImporter'


def default_adb():
    try:
        import adbutils
        return adbutils.adb_path()
    except (ImportError, RuntimeError):
        return 'adb.exe'


@dataclass
class Settings:
    adb: str = ''
    serial: str = ''
    account: str = ''
    folder: str = ''

    def save(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(path)

    @classmethod
    def load(cls, path):
        path = Path(path)
        if not path.exists():
            return cls()
        try:
            return cls(**json.loads(path.read_text(encoding='utf-8')))
        except (TypeError, json.JSONDecodeError, UnicodeError) as exc:
            raise ValueError('设置文件无法读取，请备份后重新配置。') from exc


def start_problem(settings, profile, confirmed=False, auto=False):
    if not settings.account.strip() or not confirmed:
        return '请填写账号标签，并确认模拟器当前登录的是该账号。'
    if not settings.adb or not settings.serial:
        return '请先连接并选择模拟器。'
    try:
        profile.validate(auto=auto, context=profile.verification_context(settings.account, settings.serial, settings.adb))
    except (ValueError, TypeError) as exc:
        return str(exc)
    return ''


class InstanceLock:
    """Keep a second application from recovering a live instance's queue."""
    def __init__(self, directory):
        self.path = Path(directory) / '.instance.lock'
        self.stream = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open('a+b')
        if stream.seek(0, 2) == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            stream.close()
            raise RuntimeError('这个任务数据目录已被另一窗口使用，请回到已打开的表情搬运助手。') from exc
        self.stream = stream

    def close(self):
        if self.stream:
            self.stream.close()
            self.stream = None
