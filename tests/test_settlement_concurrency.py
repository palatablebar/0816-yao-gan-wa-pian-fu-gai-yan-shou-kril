from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from app.business import DomainIssue
from app.settlement.service import open_ledger, settle
from app.settlement.store import SettlementStore


def payload():
    return {
        "region": {"x0": 0, "y0": 0, "x1": 3, "y1": 2},
        "tiles": [
            {"id": "A1", "supplier": "甲", "grade": 2, "x0": 0, "y0": 0, "x1": 3, "y1": 2}
        ],
    }


def test_parallel_close_has_single_winner(tmp_path):
    store = SettlementStore(tmp_path / "settle.db")
    ledger = open_ledger(store, payload())
    start = Barrier(2)

    def attempt():
        start.wait(3)
        try:
            settle(store, ledger["id"], 0)
            return "CLOSED"
        except DomainIssue as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as threads:
        results = list(threads.map(lambda _: attempt(), range(2)))

    assert sorted(results) == ["CLOSED", "STALE_SETTLEMENT"]
    record = store.load(ledger["id"])
    assert record["revision"] == 1 and record["closed"] is True
    # 关账事件只落一条，重复提交不会重复付款。
    assert len(store.list_events(ledger["id"])) == 1


def test_loser_can_recompute_with_fresh_revision_and_succeed(tmp_path):
    store = SettlementStore(tmp_path / "settle.db")
    ledger = open_ledger(store, payload())
    settle(store, ledger["id"], 0)  # 赢家先关账

    # 输家拿着旧结果被退回，刷新到 revision=1 后账已关，必须先重开。
    late = payload()
    late["tiles"].append(
        {"id": "B1", "supplier": "乙", "grade": 4, "x0": 1, "y0": 0, "x1": 3, "y1": 2}
    )
    from app.settlement.service import accept_late_tiles, preview

    accept_late_tiles(store, ledger["id"], late)
    snapshot = preview(store, ledger["id"])
    # 首次关账的 6 像元已付款并全部保留给甲，没有未付款像元可重算。
    assert snapshot["totals"]["paid"] == 6
    again = settle(store, ledger["id"], 1)
    assert again["revision"] == 2 and again["closed"] is True
