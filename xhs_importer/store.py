import sqlite3
from pathlib import Path
from datetime import datetime, timezone

STATES = {'pending', 'running', 'submitting', 'uncertain', 'success', 'failed', 'skipped'}


def can_retry_unsubmitted(row):
    return (row['status'] == 'failed' and row['attempt_submitted'] == 0
            and bool(row['attempt_context']) and row['attempt_kind'] in {'auto', 'trial'})


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class Store:
    """One connection per thread. Every transition commits before remote work."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('''CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY, account TEXT NOT NULL, digest TEXT NOT NULL,
            path TEXT NOT NULL, format TEXT NOT NULL, width INTEGER, height INTEGER,
            frames INTEGER, size INTEGER, status TEXT NOT NULL DEFAULT 'pending',
            note TEXT NOT NULL DEFAULT '', updated TEXT NOT NULL,
            UNIQUE(account, digest))''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY, item_id INTEGER, status TEXT, note TEXT, created TEXT)''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS staged_media (
            serial TEXT NOT NULL, remote TEXT NOT NULL, item_id INTEGER NOT NULL,
            cleaned INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(serial,remote))''')
        columns = {r['name'] for r in self.db.execute('PRAGMA table_info(items)')}
        for name, definition in [('attempt_context', "TEXT NOT NULL DEFAULT ''"),
                                 ('attempt_kind', "TEXT NOT NULL DEFAULT ''"),
                                 ('attempt_submitted', 'INTEGER NOT NULL DEFAULT 0'),
                                 ('attempt_visual', 'INTEGER NOT NULL DEFAULT 0'),
                                 ('recovery_queued', 'INTEGER NOT NULL DEFAULT 0'),
                                 ('setup_sample', 'INTEGER NOT NULL DEFAULT 0')]:
            if name not in columns:
                self.db.execute(f'ALTER TABLE items ADD COLUMN {name} {definition}')
        self.db.commit()

    def add(self, account, items, *, duplicates=None, setup_sample=False):
        if not account.strip():
            raise ValueError('请填写账号标签，用于区分导入记录。')
        added = duplicate = invalid = 0
        with self.db:
            for item in items:
                if item.error:
                    invalid += 1
                    continue
                cursor = self.db.execute('''INSERT OR IGNORE INTO items
                    (account,digest,path,format,width,height,frames,size,updated,setup_sample)
                    VALUES (?,?,?,?,?,?,?,?,?,?)''',
                    (account, item.digest, str(item.path), item.format, item.width,
                     item.height, item.frames, item.size, now(),int(setup_sample)))
                if cursor.rowcount:
                    added += 1
                else:
                    duplicate += 1
                    if duplicates is not None:
                        existing = self.db.execute('SELECT id,status,path FROM items WHERE account=? AND digest=?',
                                                   (account, item.digest)).fetchone()
                        duplicates.append({'path': str(item.path), 'existing_path': existing['path'],
                                           'id': existing['id'], 'status': existing['status']})
                    # Same bytes at a new location: preserve outcome/history; never redirect active work.
                    self.db.execute("UPDATE items SET path=? WHERE account=? AND digest=? AND status NOT IN ('running','submitting')",
                                    (str(item.path), account, item.digest))
        return added, duplicate, invalid

    def rows(self, account):
        return [dict(r) for r in self.db.execute('SELECT * FROM items WHERE account=? ORDER BY id', (account,))]

    def exception_rows(self, account):
        return [dict(r) for r in self.db.execute('''SELECT * FROM items WHERE account=? AND
            (status IN ('failed','skipped','uncertain') OR
             (recovery_queued=1 AND status IN ('pending','running','submitting'))) ORDER BY id''', (account,))]

    def begin_attempt(self, item_id, context, kind='trial', note=''):
        if not context or kind not in {'trial', 'auto'}:
            raise ValueError('缺少本次试导的环境记录。')
        with self.db:
            cursor = self.db.execute('''UPDATE items SET status='running', attempt_context=?,
                attempt_kind=?, attempt_submitted=0, attempt_visual=0, note=?, updated=? WHERE id=? AND status='pending' ''',
                (context, kind, note, now(), item_id))
            if cursor.rowcount != 1:
                raise ValueError('只能开始待处理任务，请先核对现有记录。')
            self.db.execute('INSERT INTO events(item_id,status,note,created) VALUES (?,?,?,?)',
                            (item_id, 'running', note, now()))

    def record_visual(self, item_id):
        with self.db:
            cursor = self.db.execute("UPDATE items SET attempt_visual=1 WHERE id=? AND status='submitting' AND attempt_submitted=1", (item_id,))
            if cursor.rowcount != 1:
                raise ValueError('只有本次已提交的任务才能记录视觉证据。')

    def verified_formats(self, account, context, require_visual=False):
        if not context:
            return set()
        return {r['format'] for r in self.db.execute('''SELECT DISTINCT format FROM items
            WHERE account=? AND status='success' AND attempt_context=?
            AND attempt_kind='trial' AND attempt_submitted=1
            AND (?=0 OR attempt_visual=1)''', (account, context, int(require_visual)))}

    def confirm_trials(self, item_ids, account, context):
        from .trial_batch import batch_rows, has_trial_proof
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            rows=batch_rows(self.rows(account),item_ids)
            if not context or not all(has_trial_proof(row,context) for row in rows):
                raise ValueError('本轮仍有未完成或缺少核对证据的测试，暂不能启用自动搬运。')
            for row in rows:
                if row['status']=='success':
                    continue
                note='用户已统一确认本轮测试图片收藏、手机同步及动画正常。'
                self.db.execute("UPDATE items SET status='success',note=?,updated=? WHERE id=? AND account=?",
                                (note,now(),row['id'],account))
                self.db.execute('INSERT INTO events(item_id,status,note,created) VALUES (?,?,?,?)',
                                (row['id'],'success',note,now()))
        return {row['format'] for row in rows}

    def reserve_calibration(self, item_id, account):
        note = '手动适配样例：请核对是否已收藏；未核对前不会重新导入。'
        with self.db:
            if self.db.execute("SELECT 1 FROM items WHERE account=? AND status IN ('uncertain','running','submitting')",
                               (account,)).fetchone():
                raise ValueError('请先核对已有样例或中断任务，再暂存另一张。')
            cursor = self.db.execute('''UPDATE items SET status='uncertain', attempt_kind='calibration',
                attempt_context='', attempt_submitted=0, note=?, updated=?
                WHERE id=? AND account=? AND status='pending' ''', (note, now(), item_id, account))
            if cursor.rowcount != 1:
                raise ValueError('请选择当前账号的一张待处理图片。')
            self.db.execute('INSERT INTO events(item_id,status,note,created) VALUES (?,?,?,?)',
                            (item_id, 'uncertain', note, now()))

    def set_status(self, item_id, status, note=''):
        if status not in STATES:
            raise ValueError('无效任务状态')
        with self.db:
            cursor = self.db.execute('''UPDATE items SET status=?,note=?,updated=?,
                attempt_submitted=CASE WHEN ?='submitting' THEN 1 ELSE attempt_submitted END WHERE id=?''',
                (status, note, now(), status, item_id))
            if cursor.rowcount != 1:
                raise ValueError('任务不存在')
            self.db.execute('INSERT INTO events(item_id,status,note,created) VALUES (?,?,?,?)',
                            (item_id, status, note, now()))

    def recover(self, account):
        rows = self.db.execute("SELECT id FROM items WHERE account=? AND status IN ('running','submitting')",
                               (account,)).fetchall()
        for row in rows:
            self.set_status(row['id'], 'uncertain', '上次操作中断，请到小红书核对后再继续。')
        return len(rows)

    def resolve(self, item_id, imported: bool):
        row = self.db.execute('SELECT account FROM items WHERE id=?', (item_id,)).fetchone()
        if row is None:
            raise ValueError('任务不存在。')
        self.resolve_many([item_id], row['account'], imported)

    def resolve_many(self, item_ids, account, imported: bool):
        self._review_selection(item_ids, account, 'success' if imported else 'pending')

    def park_many(self, item_ids, account):
        self._review_selection(item_ids, account, 'skipped')

    def _review_selection(self, item_ids, account, outcome):
        if not item_ids or len(set(item_ids)) != len(item_ids):
            raise ValueError('请选择不重复的异常任务。')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            rows = []
            for item_id in item_ids:
                row = self.db.execute('SELECT * FROM items WHERE id=? AND account=?', (item_id, account)).fetchone()
                eligible = row is not None and (row['status'] in {'failed','skipped','uncertain'} or
                    (outcome == 'skipped' and row['status'] == 'pending' and row['recovery_queued']))
                if not eligible:
                    raise ValueError('所选任务状态或账号已变化；只能处理当前账号的异常项，请刷新后重新选择。')
                rows.append(row)
            for row in rows:
                if outcome == 'skipped':
                    note = row['note'] if row['status'] == 'skipped' else '用户暂不重试；收藏结果仍需核对。 原因：' + row['note']
                else:
                    note = '用户已核对收藏结果。' if outcome == 'success' else '用户确认未导入，重新排队。'
                self.db.execute('UPDATE items SET status=?,recovery_queued=?,note=?,updated=? WHERE id=? AND account=?',
                    (outcome, int(outcome == 'pending'), note, now(), row['id'], account))
                self.db.execute('INSERT INTO events(item_id,status,note,created) VALUES (?,?,?,?)',
                    (row['id'], outcome, note, now()))

    def retry_unsubmitted(self, item_id, account):
        note = '用户再次选择导入；上次尚未进入选图/确认阶段，重新排队。'
        with self.db:
            cursor = self.db.execute('''UPDATE items SET status='pending',note=?,updated=?
                WHERE id=? AND account=? AND status='failed' AND attempt_submitted=0
                AND attempt_context<>'' AND attempt_kind IN ('auto','trial')''',
                (note, now(), item_id, account))
            if cursor.rowcount != 1:
                raise ValueError('此任务不能直接重试；请先核对是否已收藏。')
            self.db.execute('INSERT INTO events(item_id,status,note,created) VALUES (?,?,?,?)',
                (item_id, 'pending', note, now()))

    def skip(self, item_id, account, automatic=False):
        with self.db:
            row = self.db.execute('SELECT status,note,attempt_submitted FROM items WHERE id=? AND account=?', (item_id,account)).fetchone()
            if row is None or row['status'] not in {'pending','failed','uncertain'}:
                raise ValueError('只能跳过当前账号的待处理、失败或待核对任务。')
            result = '是否已收藏未确认，不会自动重试。' if row['attempt_submitted'] else '尚未确认收藏，不会自动重试。'
            note = ('自动跳过；' if automatic else '用户选择跳过；') + result + ' 原因：' + (row['note'] or '本次不处理。')
            cursor = self.db.execute("UPDATE items SET status='skipped',note=?,updated=? WHERE id=? AND account=? AND status=?",
                                     (note,now(),item_id,account,row['status']))
            if cursor.rowcount != 1:
                raise ValueError('任务状态已变化，请刷新后重试。')
            self.db.execute('INSERT INTO events(item_id,status,note,created) VALUES (?,?,?,?)', (item_id,'skipped',note,now()))

    def close(self):
        self.db.close()

    def record_staged(self, item_id, serial, remote):
        from .staging_cleanup import owned_path
        row = self.db.execute('SELECT digest FROM items WHERE id=?', (item_id,)).fetchone()
        if row is None or not serial:
            raise ValueError('缺少暂存图的任务或设备记录。')
        owned_path(remote, row['digest'])
        with self.db:
            existing = self.db.execute('SELECT item_id FROM staged_media WHERE serial=? AND remote=?', (serial,remote)).fetchone()
            if existing and existing['item_id'] != item_id:
                raise ValueError('暂存图已属于其他任务，保留文件。')
            self.db.execute('INSERT OR IGNORE INTO staged_media(serial,remote,item_id) VALUES (?,?,?)', (serial,remote,item_id))

    def cleanup_candidates(self, account, serial, item_id=None):
        return [dict(row) for row in self.db.execute('''SELECT s.remote,s.item_id,i.digest
            FROM staged_media s JOIN items i ON i.id=s.item_id
            WHERE s.serial=? AND s.cleaned=0 AND i.account=? AND i.status='success'
            AND (? IS NULL OR i.id=?)''', (serial,account,item_id,item_id))]

    def finish_cleanup(self, serial, remote, error=''):
        with self.db:
            self.db.execute('UPDATE staged_media SET cleaned=?,error=? WHERE serial=? AND remote=?',
                (int(not error),error,serial,remote))
