from xhs_importer.batch_progress import BatchProgress


def row(item_id, status, account='main'):
    return {'id': item_id, 'status': status, 'account': account}


def test_batch_counts_only_new_success_and_keeps_exceptions_in_remaining():
    initial = [row(1, 'pending'), row(2, 'pending'), row(3, 'pending'),
               row(4, 'pending'), row(5, 'success')]
    batch = BatchProgress.capture('main', [1, 2, 3, 4, 5], initial)
    current = [row(1, 'success'), row(2, 'failed'), row(3, 'skipped'),
               row(4, 'uncertain'), row(5, 'success'), row(99, 'success')]
    result = batch.summary('main', current)
    assert (result.total, result.success, result.remaining) == (5, 1, 4)
    assert (result.failed, result.skipped, result.uncertain, result.already_success) == (1, 1, 1, 1)
    assert result.percent == 20


def test_batch_is_stable_when_selection_changes_and_hidden_for_another_account():
    batch = BatchProgress.capture('main', [1, 2], [row(1, 'pending'), row(2, 'pending')])
    result = batch.summary('main', [row(1, 'success'), row(2, 'running'), row(3, 'success')])
    assert (result.total, result.success, result.remaining) == (2, 1, 1)
    assert batch.summary('other', [row(1, 'success', 'other')]) is None


def test_missing_rows_are_not_counted_as_success_and_empty_batch_has_no_progress():
    batch = BatchProgress.capture('main', [1, 2], [row(1, 'pending'), row(2, 'pending')])
    result = batch.summary('main', [row(1, 'success')])
    assert (result.total, result.success, result.remaining, result.missing) == (2, 1, 1, 1)
    assert BatchProgress.capture('main', [], []).summary('main', []).percent == 0
