import argparse
import json
from pathlib import Path
import sys

from .config import default_data_dir, default_adb, InstanceLock


def main():
    parser = argparse.ArgumentParser(description='Windows 小红书表情导入与适配工具')
    parser.add_argument('--data-dir', type=Path, default=default_data_dir())
    parser.add_argument('--self-check', type=Path, help='写入运行依赖检测结果后退出')
    parser.add_argument('--smoke-test', type=Path, help='启动并关闭窗口，写入检查结果')
    args = parser.parse_args()
    if args.self_check:
        import tkinter
        import PIL
        import uiautomator2
        from uiautomator2.version import __version__ as u2_version
        adb = default_adb()
        result = {'python': sys.version.split()[0], 'tk': tkinter.TkVersion,
                  'pillow': PIL.__version__, 'uiautomator2': u2_version,
                  'adb_exists': Path(adb).is_file(), 'live_import_verified': False}
        args.self_check.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        return
    import tkinter as tk
    from tkinter import messagebox
    from .gui import Application
    root = tk.Tk()
    lock = InstanceLock(args.data_dir)
    try:
        lock.acquire()
        app = Application(root, args.data_dir)
        app.wechat_panel.discovery_id = root.after(200, app.wechat_panel.discover)
    except Exception as exc:
        messagebox.showerror('启动失败', str(exc), parent=root)
        root.destroy()
        lock.close()
        return
    if args.smoke_test:
        def finish():
            args.smoke_test.write_text(json.dumps({'gui_started': True, 'profile_tested': app.profile.tested}), encoding='utf-8')
            app.close()
        root.after(800, finish)
    try:
        root.mainloop()
    finally:
        lock.close()


if __name__ == '__main__':
    main()
