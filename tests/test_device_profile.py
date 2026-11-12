import json
import pytest
from xhs_importer.profile import Profile, FlowStep, unique_node, matching_nodes
from xhs_importer.device import parse_devices, Adb


XML = '''<hierarchy><node text="添加" resource-id="id/add" bounds="[0,0][20,20]"/>
<node text="图片" resource-id="id/pic" bounds="[20,0][40,20]"/>
<node text="图片" resource-id="id/pic" bounds="[40,0][60,20]"/>
<node text="hidden" bounds="[0,0][0,0]"/></hierarchy>'''


def make_profile():
    p = Profile(package='app.xhs', version='1.0',
        favorites={'resource-id': 'id/favorites'},
        open_steps=[FlowStep('click', {'resource-id': 'id/add'}, package='app.xhs')],
        pick_steps=[FlowStep('target', {'text': '{filename}'}, package='app.xhs')],
        submit_steps=[FlowStep('click', {'text': '确定'}, package='app.xhs')])
    return p


def test_ambiguous_and_hidden_nodes_are_never_clicked():
    assert unique_node(XML, {'text': '添加'})['resource-id'] == 'id/add'
    with pytest.raises(ValueError, match='2'):
        unique_node(XML, {'resource-id': 'id/pic'})
    assert matching_nodes(XML, {'text': 'hidden'}) == []
    with pytest.raises(ValueError):
        matching_nodes(XML, {})


def test_device_selection_lists_states_without_silently_picking_first():
    parsed = parse_devices('List of devices attached\nemulator-5554 device product:ld model:LD\nemulator-5556 offline\nabc unauthorized\n')
    assert [(d.serial, d.state) for d in parsed] == [('emulator-5554', 'device'), ('emulator-5556', 'offline'), ('abc', 'unauthorized')]


def test_profile_requires_deterministic_target_and_calibration_for_auto(tmp_path):
    p = make_profile()
    p.validate()
    with pytest.raises(ValueError):
        p.validate(auto=True)
    p.pick_steps[0] = FlowStep('target', {'resource-id': 'first_picture'}, package='app.xhs')
    with pytest.raises(ValueError, match='文件名|相册'):
        p.validate()
    p.album_marker = {'text': '{album}'}
    p.validate()
    p.save(tmp_path / 'profile.json')
    loaded = Profile.load(tmp_path / 'profile.json')
    assert loaded.pick_steps[0].action == 'target'
    assert loaded.album_marker == {'text': '{album}'}


def test_profile_rejects_code_and_unknown_action():
    p = make_profile()
    p.open_steps.append(FlowStep('shell', {'text': 'x'}, value='rm -rf /', package='app.xhs'))
    with pytest.raises(ValueError):
        p.validate()


def test_shell_arguments_cannot_execute_embedded_metacharacters(monkeypatch):
    # Real adapter builds command; replace only the external process boundary.
    from subprocess import CompletedProcess
    captured = []
    def execute(args, **kwargs):
        captured.append(args)
        return CompletedProcess(args, 0, 'ok', '')
    monkeypatch.setattr('xhs_importer.device.subprocess.run', execute)
    adb = Adb('adb.exe')
    adb.shell('emulator-5554', ['echo', "name'; reboot; '"])
    import shlex
    assert shlex.split(captured[0][-1]) == ['echo', "name'; reboot; '"]
    assert captured[0][:4] == ['adb.exe', '-s', 'emulator-5554', 'shell']


def test_connect_restricts_default_to_local_emulator():
    with pytest.raises(ValueError):
        Adb('adb.exe').connect('192.168.1.20:5555')


def test_auto_verification_belongs_to_account_device_and_profile():
    p = make_profile()
    p.result = {'text': 'new result'}
    p.tested, p.phone_sync, p.tested_formats = True, True, ['PNG']
    context = p.verification_context('main', 'emulator-1', 'adb.exe')
    p.tested_context = context
    p.validate(auto=True, context=context)
    for account, device, adb in [('other', 'emulator-1', 'adb.exe'),
                                 ('main', 'emulator-2', 'adb.exe'),
                                 ('main', 'emulator-1', 'other-adb.exe')]:
        with pytest.raises(ValueError, match='实测'):
            p.validate(auto=True, context=p.verification_context(account, device, adb))
    p.version = '2.0'
    with pytest.raises(ValueError, match='实测'):
        p.validate(auto=True, context=p.verification_context('main', 'emulator-1', 'adb.exe'))


def test_each_staging_attempt_has_a_unique_picker_filename(monkeypatch, tmp_path):
    import shlex
    from subprocess import CompletedProcess
    paths = []
    source = tmp_path / '原始图片.png'
    source.write_bytes(b'original bytes')
    def execute(args, **kwargs):
        if 'push' in args:
            paths.append(args[-1])
        output = ''
        if 'shell' in args and shlex.split(args[-1])[:2] == ['content', 'query']:
            output = f'Row: 0 _id=42, _data={paths[-1]}'
        return CompletedProcess(args, 0, output, '')
    monkeypatch.setattr('xhs_importer.device.subprocess.run', execute)
    adb = Adb('adb.exe')
    first = adb.stage('emulator-1', source, 'a' * 64)
    second = adb.stage('emulator-1', source, 'a' * 64)
    assert first['filename'] != second['filename'], 'Repeated trials must not create ambiguous named targets'
    assert first['album'] != second['album']
    assert all(path.startswith('/sdcard/DCIM/XHSImport_') for path in paths), 'LDPlayer Pictures is an unscanned shared-directory symlink'
    assert source.read_bytes() == b'original bytes'
