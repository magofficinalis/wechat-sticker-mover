from pathlib import Path
from xhs_importer.config import Settings, start_problem
from xhs_importer.profile import Profile


def test_settings_round_trip_and_reject_corrupt_file(tmp_path):
    path = tmp_path / '设置.json'
    s = Settings(adb='D:/程序/adb.exe', serial='emulator-5554', account='我的账号', folder='D:/图片')
    s.save(path)
    assert Settings.load(path) == s
    path.write_text('{broken', encoding='utf-8')
    import pytest
    with pytest.raises(ValueError):
        Settings.load(path)


def test_start_gate_requires_current_account_confirmation():
    settings = Settings(adb='adb.exe', serial='emulator-5554', account='main')
    assert '账号' in start_problem(settings, Profile(), confirmed=False)


def test_start_gate_requires_real_profile_not_placeholder():
    settings = Settings(adb='adb.exe', serial='emulator-5554', account='main')
    assert start_problem(settings, Profile(), confirmed=True)
