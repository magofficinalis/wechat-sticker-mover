import json
from pathlib import Path
import subprocess
import sys
import pytest


def test_self_check_uses_actual_installed_dependency_api(tmp_path):
    report = tmp_path / 'check.json'
    proc = subprocess.run([sys.executable, 'launch.py', '--self-check', str(report)], capture_output=True)
    assert proc.returncode == 0, proc.stderr.decode(errors='replace')
    assert json.loads(report.read_text())['live_import_verified'] is False


def test_second_instance_cannot_recover_running_first_instance(tmp_path):
    from xhs_importer.config import InstanceLock
    first = InstanceLock(tmp_path)
    first.acquire()
    second = InstanceLock(tmp_path)
    try:
        with pytest.raises(RuntimeError):
            second.acquire()
    finally:
        first.close()
    second.acquire()
    second.close()
