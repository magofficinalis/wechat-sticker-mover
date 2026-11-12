import threading
from pathlib import Path
import pytest
from PIL import Image
from xhs_importer.catalog import scan_folder
from xhs_importer.store import Store
from xhs_importer.engine import ImportEngine
from xhs_importer.xhs_adapter import make_profile, PACKAGE

FIXTURES = Path(__file__).parent / 'fixtures' / 'xhs-9.33.4'


class VisualPhone:
    def __init__(self, evidence=True, stale=False):
        self.screen = 'favorites-panel.xml'
        self.evidence, self.stale = evidence, stale
        self.clicks = []
    def identity(self, package): return {'version':'9.33.4','serial':'test'}
    def current_package(self): return PACKAGE
    def hierarchy(self): return (FIXTURES / self.screen).read_text(encoding='utf-8')
    def stage(self, row): return {'album':'XHSImport_108bb598befa','filename':'test.png'}
    def cleanup_staging(self, *args, **kwargs): return 0
    def prepare_visual(self, path):
        if self.stale: raise ValueError('stale evidence')
        return object()
    def verify_visual(self, evidence): return self.evidence
    def select_album(self, album, stop): self.click({'text':album,'package':PACKAGE})
    def click(self, selector):
        self.clicks.append(selector)
        if selector.get('xhs') == 'favorites-add': self.screen = 'album-picker.xml'
        elif selector.get('xhs') == 'album-menu': self.screen = 'single-album.xml'
        elif selector.get('xhs') == 'album-image': self.screen = 'preview.xml'
        elif selector.get('text') == '添加': self.screen = 'favorites-panel.xml'


def scenario(tmp_path, phone):
    Image.new('RGB',(30,30),'red').save(tmp_path/'one.png')
    store=Store(tmp_path/'db');store.add('test',scan_folder(tmp_path))
    profile=make_profile('9.33.4')
    engine=ImportEngine(store,phone,profile,'test',lambda *args:None,threading.Event())
    engine.visual_timeout = .01
    return store,engine


@pytest.mark.parametrize('evidence',[True,False])
def test_visual_trial_records_actual_proof_and_always_requires_user(tmp_path,evidence):
    store,engine=scenario(tmp_path,VisualPhone(evidence))
    engine.run()
    row=store.rows('test')[0]
    assert row['status']=='uncertain'
    assert row['attempt_visual']==int(evidence)
    assert row['attempt_submitted']==1


def test_stale_visual_evidence_blocks_before_any_click(tmp_path):
    phone=VisualPhone(stale=True);store,engine=scenario(tmp_path,phone)
    engine.run()
    assert store.rows('test')[0]['status']=='failed'
    assert phone.clicks==[]


def test_profile_flags_alone_do_not_unlock_visual_batch(tmp_path):
    store,engine=scenario(tmp_path,VisualPhone())
    p=engine.profile;p.tested=True;p.phone_sync=True;p.tested_formats=['PNG']
    p.tested_context=p.verification_context('test','test')
    with pytest.raises(ValueError): engine.run(auto_verify=True)
    assert store.rows('test')[0]['status']=='pending'


def test_pause_during_visual_capture_does_not_record_proof(tmp_path):
    phone=VisualPhone();store,engine=scenario(tmp_path,phone)
    def observe(evidence):
        engine.stop.set()
        return True
    phone.verify_visual=observe
    engine.run()
    row=store.rows('test')[0]
    assert row['status']=='uncertain'
    assert row['attempt_visual']==0


@pytest.mark.parametrize('evidence',[True,False])
def test_auto_advances_only_with_visual_proof(tmp_path,evidence):
    phone=VisualPhone(evidence);store,engine=scenario(tmp_path,phone)
    Image.new('RGB',(30,30),'blue').save(tmp_path/'two.png')
    Image.new('RGB',(30,30),'green').save(tmp_path/'three.png')
    store.add('test',scan_folder(tmp_path))
    rows=store.rows('test');context=engine.profile.verification_context('test','test')
    store.begin_attempt(rows[0]['id'],context)
    store.set_status(rows[0]['id'],'submitting');store.record_visual(rows[0]['id'])
    store.set_status(rows[0]['id'],'uncertain');store.resolve(rows[0]['id'],True)
    p=engine.profile;p.tested=True;p.phone_sync=True;p.tested_formats=['PNG'];p.tested_context=context
    engine.run(limit=5,auto_verify=True)
    assert store.rows('test')[1]['status']==('success' if evidence else 'uncertain')
    assert store.rows('test')[2]['status']==('success' if evidence else 'pending')
    assert sum(s.get('text')=='添加' for s in phone.clicks)==(2 if evidence else 1)


class SourceCheckingPhone(VisualPhone):
    def prepare_visual(self, path):
        from xhs_importer.device import AndroidDevice
        return AndroidDevice.prepare_visual(self, path)

    def visual_observation(self):
        return False, None


def long_gif(path):
    frames = [Image.new('RGB', (12, 12), (index, 20, 30)) for index in range(223)]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=20, optimize=False)
    with Image.open(path) as image:
        assert image.n_frames == 223


@pytest.mark.parametrize('source_kind', ['long_gif', 'uniform'])
def test_manual_retry_of_unverifiable_source_stops_for_confirmation_without_qualification(tmp_path, source_kind):
    phone = SourceCheckingPhone()
    store, engine = scenario(tmp_path, phone)
    if source_kind == 'long_gif':
        path = tmp_path / 'long.gif'
        long_gif(path)
        store.add('test', scan_folder(tmp_path))
        row = store.rows('test')[-1]
    else:
        row = store.rows('test')[0]
        path = Path(row['path'])
    original_bytes = path.read_bytes()
    store.skip(row['id'], 'test')
    store.resolve(row['id'], False)
    events = []
    engine.notify = lambda *event: events.append(event)
    engine.run(item_id=row['id'])
    current = next(r for r in store.rows('test') if r['id'] == row['id'])
    assert current['status'] == 'uncertain'
    assert current['attempt_submitted'] == 1
    assert current['attempt_visual'] == 0
    assert sum(s.get('text') == '添加' for s in phone.clicks) == 1
    assert any(kind == 'needs-review' for kind, message in events)
    assert path.read_bytes() == original_bytes
    store.resolve(row['id'], True)
    assert store.verified_formats('test', current['attempt_context'], require_visual=True) == set()


def test_manual_unverifiable_source_never_advances_without_user(tmp_path):
    phone = SourceCheckingPhone()
    store, engine = scenario(tmp_path, phone)
    long_gif(tmp_path / 'next.gif')
    store.add('test', scan_folder(tmp_path))
    engine.run(limit=5)
    assert [r['status'] for r in store.rows('test')] == ['uncertain', 'pending']
    assert sum(s.get('text') == '添加' for s in phone.clicks) == 1


def test_automatic_unverifiable_source_still_blocks_before_staging(tmp_path):
    phone = SourceCheckingPhone()
    store, engine = scenario(tmp_path, phone)
    path = tmp_path / 'long.gif'
    long_gif(path)
    store.add('test', scan_folder(tmp_path))
    target = store.rows('test')[-1]
    # A separate, qualified GIF trial is necessary to exercise this automatic branch.
    Image.new('RGB', (12,12), 'green').save(tmp_path / 'qualified.gif')
    store.add('test', scan_folder(tmp_path))
    qualified = store.rows('test')[-1]
    context = engine.profile.verification_context('test', 'test')
    store.begin_attempt(qualified['id'], context)
    store.set_status(qualified['id'], 'submitting')
    store.record_visual(qualified['id'])
    store.set_status(qualified['id'], 'uncertain')
    store.resolve(qualified['id'], True)
    profile = engine.profile
    profile.tested, profile.phone_sync = True, True
    profile.tested_formats, profile.tested_context = ['GIF'], context
    phone.stage = lambda row: pytest.fail('automatic mode must reject before staging')
    engine.run(item_id=target['id'], auto_verify=True)
    current = next(r for r in store.rows('test') if r['id'] == target['id'])
    assert current['status'] == 'failed'
    assert current['attempt_submitted'] == 0
    assert current['attempt_visual'] == 0
    assert phone.clicks == []


def test_manual_corrupt_source_error_is_not_treated_as_visual_limit(tmp_path):
    phone = VisualPhone()
    store, engine = scenario(tmp_path, phone)
    def broken_source(path):
        raise OSError('broken image stream')
    phone.prepare_visual = broken_source
    engine.run()
    row = store.rows('test')[0]
    assert row['status'] == 'failed'
    assert row['attempt_submitted'] == 0
    assert phone.clicks == []


@pytest.mark.parametrize('problem', ['unknown_ui', 'lingering_banner'])
def test_manual_source_limit_does_not_bypass_ui_preflight(tmp_path, monkeypatch, problem):
    from types import SimpleNamespace
    phone = SourceCheckingPhone()
    store, engine = scenario(tmp_path, phone)
    if problem == 'unknown_ui':
        def unknown():
            raise ValueError('收藏首项结构变化，停止核对。')
        phone.visual_observation = unknown
    else:
        phone.visual_observation = lambda: (True, None)
        ticks = iter([0, 7])
        monkeypatch.setattr('xhs_importer.device.time', SimpleNamespace(
            monotonic=lambda: next(ticks), sleep=lambda duration: None))
    engine.run()
    row = store.rows('test')[0]
    assert row['status'] == 'failed'
    assert row['attempt_submitted'] == 0
    assert phone.clicks == []


@pytest.mark.parametrize('toast', [True, False])
def test_real_evidence_routes_are_recorded_by_engine(tmp_path, toast):
    from xhs_importer.visual import VisualEvidence
    class Matcher:
        def matches(self, tile): return tile == 'target'
        def distinct_from(self, tile): return tile == 'other'
    class EvidencePhone(VisualPhone):
        ticks = 1.0
        awaiting_capture = False
        def click(self, selector):
            super().click(selector)
            if selector.get('text') == '添加':
                self.awaiting_capture = True
        def hierarchy(self):
            assert not self.awaiting_capture, 'capture the transient toast before another XML read'
            return super().hierarchy()
        def prepare_visual(self, path):
            e = VisualEvidence(Matcher(), clock=lambda: self.ticks)
            e.begin(False, 'other')
            return e
        def capture_visual_banner(self, evidence):
            assert self.clicks[-1].get('text') == '添加'
            evidence.observe_banner(toast)
            self.awaiting_capture = False
        def verify_visual(self, evidence):
            self.ticks += .9
            return evidence.observe(False, 'target')
    phone = EvidencePhone()
    store, engine = scenario(tmp_path, phone)
    engine.visual_timeout = 2
    engine.run()
    row = store.rows('test')[0]
    assert row['status'] == 'uncertain'
    assert row['attempt_visual'] == 1
    assert ('成功提示' if toast else '连续') in row['note']
    store.resolve(row['id'], True)
    profile = engine.profile
    profile.tested = profile.phone_sync = True
    profile.tested_formats = ['PNG']
    profile.tested_context = profile.verification_context('test', 'test')
    for name, color in [('two.png', 'blue'), ('three.png', 'green')]:
        Image.new('RGB', (30,30), color).save(tmp_path / name)
    store.add('test', scan_folder(tmp_path))
    engine.run(limit=2, auto_verify=True)
    assert [r['status'] for r in store.rows('test')] == ['success'] * 3
    assert all(r['attempt_visual'] == 1 for r in store.rows('test'))
    assert sum(s.get('text') == '添加' for s in phone.clicks) == 3


def test_visual_timeout_persists_specific_diagnostic(tmp_path):
    from xhs_importer.visual import VisualEvidence
    class Matcher:
        def matches(self, tile): return tile == 'target'
    phone = VisualPhone()
    evidence = VisualEvidence(Matcher())
    evidence.begin(False, 'other')
    phone.prepare_visual = lambda path: evidence
    phone.verify_visual = lambda e: e.observe(True, 'other')
    store, engine = scenario(tmp_path, phone)
    engine.run()
    row = store.rows('test')[0]
    assert row['status'] == 'uncertain'
    assert '目标缩略图' in row['note']
    assert '成功提示' in row['note']
    assert '已看到成功提示，但未匹配' in row['note']
    assert row['attempt_visual'] == 0


def test_expired_submission_window_does_not_start_another_wait(tmp_path):
    from types import SimpleNamespace
    phone = VisualPhone()
    evidence = SimpleNamespace(remaining_window=0, timeout_note='本次核对窗口已结束。')
    phone.prepare_visual = lambda path: evidence
    phone.verify_visual = lambda evidence: pytest.fail('must not poll after the attempt window expired')
    store, engine = scenario(tmp_path, phone)
    engine.run()
    row = store.rows('test')[0]
    assert row['status'] == 'uncertain'
    assert row['note'] == evidence.timeout_note
    assert row['attempt_visual'] == 0
