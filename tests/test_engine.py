import threading
from pathlib import Path
from PIL import Image
from xhs_importer.catalog import scan_folder
from xhs_importer.store import Store
from xhs_importer.profile import Profile, FlowStep
from xhs_importer.engine import ImportEngine


def node(text):
    return f'<node text="{text}" bounds="[0,0][20,20]"/>'


class Phone:
    """Only the external Android boundary is fake; engine and journal are real."""
    def __init__(self, crash=False, stale=False, version='1.0'):
        self.screen = 'favorites'
        self.crash, self.stale, self.version = crash, stale, version
        self.context = {'filename': 'abc.png', 'album': 'owned'}
        self.pressed = []

    def identity(self, package):
        return {'serial': 'emulator-1', 'package': package, 'version': self.version}

    def current_package(self):
        return 'app.xhs'

    def hierarchy(self):
        texts = {'favorites': ['favorites', 'add'], 'picker': ['abc.png'], 'preview': ['confirm'],
                 'done': ['favorites', 'added']}[self.screen]
        if self.stale and 'added' not in texts:
            texts = texts + ['added']
        return '<hierarchy>' + ''.join(node(t) for t in texts) + '</hierarchy>'

    def stage(self, row):
        return self.context

    def cleanup_staging(self, *args, **kwargs):
        return 0

    def click(self, selector):
        value = selector['text']
        self.pressed.append(value)
        if value == 'add':
            self.screen = 'picker'
        elif value == 'abc.png':
            self.screen = 'preview'
        elif value == 'confirm':
            self.screen = 'done'
            if self.crash:
                raise RuntimeError('disconnected after submit')


def scenario(tmp_path, phone=None):
    folder = tmp_path / 'pics'
    folder.mkdir()
    Image.new('RGB', (20, 20), 'red').save(folder / '1.png')
    Image.new('RGB', (20, 20), 'blue').save(folder / '2.png')
    store = Store(tmp_path / 'queue.db')
    store.add('main', scan_folder(folder))
    profile = Profile(package='app.xhs', version='1.0', favorites={'text': 'favorites'}, result={'text': 'added'},
        open_steps=[FlowStep('click', {'text': 'add'}, package='app.xhs')],
        pick_steps=[FlowStep('target', {'text': '{filename}'}, package='app.xhs')],
        submit_steps=[FlowStep('click', {'text': 'confirm'}, package='app.xhs')],
        tested=True, tested_formats=['PNG'], phone_sync=True)
    events = []
    stop = threading.Event()
    phone = phone or Phone()
    profile.tested_context = profile.verification_context('main', 'emulator-1')
    engine = ImportEngine(store, phone, profile, 'main', lambda kind, text: events.append((kind, text)), stop)
    return store, engine, phone, events, stop


def test_auto_success_requires_new_evidence_and_favorites(tmp_path):
    store, engine, phone, events, stop = scenario(tmp_path)
    engine.run(limit=1, auto_verify=True)
    assert [r['status'] for r in store.rows('main')] == ['success', 'pending']
    assert any(kind == 'completed' for kind, _ in events)


def test_stale_success_marker_stops_before_submit(tmp_path):
    store, engine, phone, events, stop = scenario(tmp_path, Phone(stale=True))
    engine.run(limit=5, auto_verify=True)
    assert [r['status'] for r in store.rows('main')] == ['failed', 'pending']
    assert phone.pressed == []


def test_disconnect_after_submit_does_not_retry_or_advance(tmp_path):
    store, engine, phone, events, stop = scenario(tmp_path, Phone(crash=True))
    engine.run(limit=5, auto_verify=True)
    assert [r['status'] for r in store.rows('main')] == ['uncertain', 'pending']
    assert phone.pressed.count('confirm') == 1


def test_manual_mode_requires_user_confirmation(tmp_path):
    store, engine, phone, events, stop = scenario(tmp_path)
    engine.run(limit=5, auto_verify=False)
    assert [r['status'] for r in store.rows('main')] == ['uncertain', 'pending']
    assert any(kind == 'needs-review' for kind, _ in events)


def test_stop_prevents_new_item(tmp_path):
    store, engine, phone, events, stop = scenario(tmp_path)
    stop.set()
    engine.run(limit=5, auto_verify=True)
    assert {r['status'] for r in store.rows('main')} == {'pending'}
    assert phone.pressed == []


def test_single_trial_can_target_selected_pending_image(tmp_path):
    store, engine, phone, events, stop = scenario(tmp_path)
    selected = store.rows('main')[1]['id']
    engine.run(limit=1, item_id=selected)
    assert [r['status'] for r in store.rows('main')] == ['pending', 'uncertain']


def test_invalid_selected_image_never_falls_back_to_first(tmp_path):
    import pytest
    store, engine, phone, events, stop = scenario(tmp_path)
    with pytest.raises(ValueError):
        engine.run(limit=1, item_id=-1)
    assert phone.pressed == []


def test_visual_proof_is_required_even_after_user_resolves_trial(tmp_path):
    store, engine, phone, events, stop = scenario(tmp_path)
    engine.run(limit=1)
    row = store.rows('main')[0]
    store.resolve(row['id'], True)
    context = row['attempt_context']
    assert store.verified_formats('main', context, require_visual=True) == set()


def test_explicit_batch_never_processes_unselected_pending_items(tmp_path):
    store, engine, phone, events, stop = scenario(tmp_path)
    Image.new('RGB',(20,20),'green').save(tmp_path/'pics'/'3.png')
    store.add('main',scan_folder(tmp_path/'pics'))
    selected=[r['id'] for r in store.rows('main')[1:]]
    original_stage=phone.stage
    def stage(row):
        phone.screen='favorites'
        return original_stage(row)
    phone.stage=stage
    # Each iteration starts from done (which contains favorites and result), so
    # staging clears the old marker to model a fresh operation.
    engine.run(limit=2,auto_verify=True,item_ids=selected)
    assert [r['status'] for r in store.rows('main')]==['pending','success','success']


def test_empty_or_unknown_explicit_batch_does_not_fall_back(tmp_path):
    import pytest
    store, engine, phone, events, stop = scenario(tmp_path)
    row_id = store.rows('main')[0]['id']
    store.add('other', scan_folder(tmp_path/'pics'))
    other_id = store.rows('other')[0]['id']
    for ids in ([],[-1],[row_id,row_id],[other_id]):
        with pytest.raises(ValueError): engine.run(item_ids=ids)
    assert phone.pressed==[]


def test_visual_evidence_is_reset_for_new_attempt(tmp_path):
    store, engine, phone, events, stop = scenario(tmp_path)
    row = store.rows('main')[0]
    import pytest
    with pytest.raises(ValueError): store.record_visual(row['id'])
    store.begin_attempt(row['id'], 'ctx')
    store.set_status(row['id'], 'submitting')
    store.record_visual(row['id'])
    store.set_status(row['id'], 'uncertain')
    store.resolve(row['id'], True)
    assert store.verified_formats('main', 'ctx', require_visual=True) == {'PNG'}
    store.set_status(row['id'], 'pending')
    store.begin_attempt(row['id'], 'ctx')
    assert store.rows('main')[0]['attempt_visual'] == 0


def test_version_change_blocks_before_staging(tmp_path):
    store, engine, phone, events, stop = scenario(tmp_path, Phone(version='2.0'))
    import pytest
    with pytest.raises(ValueError, match='版本'):
        engine.run(limit=1, auto_verify=True)
    assert {r['status'] for r in store.rows('main')} == {'pending'}


def test_changed_source_does_not_import_wrong_content(tmp_path):
    store, engine, phone, events, stop = scenario(tmp_path)
    Path(store.rows('main')[0]['path']).write_bytes(b'changed')
    engine.run(limit=1)
    assert store.rows('main')[0]['status'] == 'failed'
    assert phone.pressed == []


def test_unresolved_previous_item_blocks_new_batch(tmp_path):
    store, engine, phone, events, stop = scenario(tmp_path)
    store.set_status(store.rows('main')[0]['id'], 'uncertain')
    import pytest
    with pytest.raises(ValueError):
        engine.run(limit=5)
    assert store.rows('main')[1]['status'] == 'pending'


def test_manual_trial_evidence_is_bound_to_current_profile_and_device(tmp_path):
    store, engine, phone, events, stop = scenario(tmp_path)
    current = engine.profile.verification_context('main', 'emulator-1')
    engine.run(limit=1, auto_verify=False)
    row = store.rows('main')[0]
    assert store.verified_formats('main', current) == set()
    store.resolve(row['id'], True)
    assert store.verified_formats('main', current) == {'PNG'}
    assert store.verified_formats('main', engine.profile.verification_context('main', 'emulator-2')) == set()
    engine.profile.invalidate_verification()
    revised = engine.profile.verification_context('main', 'emulator-1')
    assert revised != current
    assert store.verified_formats('main', revised) == set()
    path = tmp_path / 'profile.json'
    engine.profile.save(path)
    assert Profile.load(path).verification_context('main', 'emulator-1') == revised


def test_failed_pre_submit_attempt_cannot_validate_a_format(tmp_path):
    store, engine, phone, events, stop = scenario(tmp_path)
    Path(store.rows('main')[0]['path']).write_bytes(b'changed')
    engine.run(limit=1)
    store.resolve(store.rows('main')[0]['id'], True)
    context = engine.profile.verification_context('main', 'emulator-1')
    assert store.verified_formats('main', context) == set()


def test_calibration_sample_is_reconciled_before_any_automatic_import(tmp_path):
    import pytest
    store, engine, phone, events, stop = scenario(tmp_path)
    row = store.rows('main')[0]
    store.reserve_calibration(row['id'], 'main')
    assert store.rows('main')[0]['status'] == 'uncertain'
    reopened = Store(store.path)
    assert reopened.rows('main')[0]['status'] == 'uncertain'
    reopened.close()
    with pytest.raises(ValueError, match='核对'):
        engine.run(limit=5)
    assert phone.pressed == []
    with pytest.raises(ValueError):
        store.reserve_calibration(store.rows('main')[1]['id'], 'main')
    store.resolve(row['id'], True)
    assert store.rows('main')[0]['status'] == 'success'
    assert store.verified_formats('main', engine.profile.verification_context('main', 'emulator-1')) == set()
    with pytest.raises(ValueError):
        store.reserve_calibration(row['id'], 'main')
    with pytest.raises(ValueError):
        store.reserve_calibration(store.rows('main')[1]['id'], 'another-account')
