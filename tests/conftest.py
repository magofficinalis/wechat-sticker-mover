import os
from pathlib import Path
import subprocess
import sys
import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    # This Windows/Anaconda Tk runtime crashes when several independent Tcl apps
    # are constructed and destroyed in one process. Match the shipped lifecycle:
    # one real Tk application per process, and assert the child exit/output.
    if (os.name != 'nt' or os.environ.get('XHS_GUI_TEST_CHILD') == '1' or
            pyfuncitem.path.name not in {'test_preview_gui.py', 'test_wechat_gui.py', 'test_exception_gui.py', 'test_product_gui.py', 'test_calibration_gui.py', 'test_trial_gui.py', 'test_scrolling_page.py', 'test_gallery_gui.py'}):
        return None
    env = dict(os.environ, XHS_GUI_TEST_CHILD='1')
    temp = pyfuncitem.funcargs['tmp_path'] / 'gui-process'
    result = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'pytest', pyfuncitem.nodeid,
        '-q', '--basetemp', str(temp)], cwd=Path(__file__).resolve().parents[1], env=env,
        capture_output=True, text=True, encoding='utf-8', timeout=30,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        pytest.fail(result.stdout + result.stderr, pytrace=False)
    return True
