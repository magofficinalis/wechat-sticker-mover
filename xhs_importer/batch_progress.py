"""Read-only progress for one explicitly dispatched batch."""
from dataclasses import dataclass


@dataclass(frozen=True)
class BatchSummary:
    total: int
    success: int
    remaining: int
    failed: int
    skipped: int
    uncertain: int
    already_success: int
    missing: int

    @property
    def percent(self):
        return 100 * self.success / self.total if self.total else 0


@dataclass(frozen=True)
class BatchProgress:
    account: str
    ids: frozenset
    initial_success: frozenset

    @classmethod
    def capture(cls, account, ids, rows):
        selected = frozenset(ids)
        already = frozenset(r['id'] for r in rows
                            if r['account'] == account and r['id'] in selected and r['status'] == 'success')
        return cls(account, selected, already)

    def summary(self, account, rows):
        if account != self.account:
            return None
        selected = {r['id']: r for r in rows if r['account'] == account and r['id'] in self.ids}
        success = sum(r['status'] == 'success' and item_id not in self.initial_success
                      for item_id, r in selected.items())
        return BatchSummary(total=len(self.ids), success=success, remaining=len(self.ids) - success,
            failed=sum(r['status'] == 'failed' for r in selected.values()),
            skipped=sum(r['status'] == 'skipped' for r in selected.values()),
            uncertain=sum(r['status'] == 'uncertain' for r in selected.values()),
            already_success=sum(r['status'] == 'success' and item_id in self.initial_success
                                for item_id, r in selected.items()),
            missing=len(self.ids) - len(selected))
