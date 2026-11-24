import json

import pytest

from app.business import DomainIssue
from app.settlement.service import accept_late_tiles, open_ledger, preview, settle
from app.settlement.store import SettlementStore


def base_payload():
    return {
        "region": {"x0": 0, "y0": 0, "x1": 4, "y1": 2},
        "tiles": [
            {"id": "A1", "supplier": "甲", "grade": 2, "x0": 0, "y0": 0, "x1": 3, "y1": 2},
            {"id": "B1", "supplier": "乙", "grade": 2, "x0": 3, "y0": 0, "x1": 4, "y1": 2},
        ],
    }


@pytest.fixture
def store(tmp_path):
    return SettlementStore(tmp_path / "settle.db")


def test_open_is_idempotent_and_keeps_history(store):
    first = open_ledger(store, base_payload())
    second = open_ledger(store, base_payload())
    assert first["id"] == second["id"] and first["revision"] == 0


def test_close_pays_all_and_records_event(store):
    ledger = open_ledger(store, base_payload())
    closed = settle(store, ledger["id"], 0)
    assert closed["closed"] is True and closed["revision"] == 1
    # 关账把 8 个可付像元全部转为已付。
    assert closed["snapshot"]["totals"] == {"paid": 0, "payable": 8, "disputed": 0}
    assert len(closed["paid"]) == 8
    events = store.list_events(ledger["id"])
    assert len(events) == 1 and events[0]["revision"] == 1


def test_close_then_late_tile_reopens_and_retains_paid_owner(store):
    ledger = open_ledger(store, base_payload())
    settle(store, ledger["id"], 0)

    late = base_payload()
    late["tiles"].append(
        {"id": "C1", "supplier": "丙", "grade": 4, "x0": 1, "y0": 0, "x1": 4, "y1": 2}
    )
    reopened = accept_late_tiles(store, ledger["id"], late)
    assert reopened["closed"] is False and reopened["revision"] == 1
    # 已付款 8 像元全部保留原领款人，无新增可付。
    result = preview(store, ledger["id"])
    assert result["totals"] == {"paid": 8, "payable": 0, "disputed": 0}
    owners = {entry["owner"] for entry in result["buckets"]["paid"]}
    assert owners == {"甲", "乙"}

def test_late_tile_only_pays_unpaid_cells_with_new_evidence(store):
    payload = {
        "region": {"x0": 0, "y0": 0, "x1": 3, "y1": 1},
        "tiles": [
            {"id": "A1", "supplier": "甲", "grade": 2, "x0": 0, "y0": 0, "x1": 3, "y1": 1}
        ],
    }
    ledger = open_ledger(store, payload)
    closed = settle(store, ledger["id"], 0)
    assert closed["snapshot"]["totals"]["payable"] == 3 and len(closed["paid"]) == 3

    late = {
        "region": payload["region"],
        "tiles": [
            payload["tiles"][0],
            {"id": "B1", "supplier": "乙", "grade": 4, "x0": 1, "y0": 0, "x1": 3, "y1": 1},
        ],
    }
    accept_late_tiles(store, ledger["id"], late)
    result = preview(store, ledger["id"])
    # 已付的 3 像元保留给甲；没有未付款像元可重算。
    assert result["totals"] == {"paid": 3, "payable": 0, "disputed": 0}


def test_stale_revision_is_rejected_and_must_recompute(store):
    ledger = open_ledger(store, base_payload())
    settle(store, ledger["id"], 0)
    late = base_payload()
    late["tiles"].append(
        {"id": "C1", "supplier": "丙", "grade": 4, "x0": 0, "y0": 0, "x1": 4, "y1": 2}
    )
    accept_late_tiles(store, ledger["id"], late)
    # 仍拿旧 revision=0 关账：过期结果退回重算。
    with pytest.raises(DomainIssue, match="STALE_SETTLEMENT"):
        settle(store, ledger["id"], 0)
    fresh = settle(store, ledger["id"], 1)
    assert fresh["revision"] == 2 and fresh["closed"] is True


def test_closed_ledger_cannot_close_again(store):
    ledger = open_ledger(store, base_payload())
    settle(store, ledger["id"], 0)
    with pytest.raises(DomainIssue, match="LEDGER_FINALIZED"):
        settle(store, ledger["id"], 1)


def test_late_tiles_require_closed_ledger(store):
    ledger = open_ledger(store, base_payload())
    with pytest.raises(DomainIssue, match="LEDGER_NOT_CLOSED"):
        accept_late_tiles(store, ledger["id"], base_payload())


def test_late_tiles_cannot_move_region(store):
    ledger = open_ledger(store, base_payload())
    settle(store, ledger["id"], 0)
    moved = base_payload()
    moved["region"] = {"x0": 0, "y0": 0, "x1": 5, "y1": 2}
    with pytest.raises(DomainIssue, match="REGION_MOVED"):
        accept_late_tiles(store, ledger["id"], moved)


def test_paid_ledger_persists_across_connections(tmp_path):
    import sqlite3

    db_path = tmp_path / "settle.db"
    store = SettlementStore(db_path)
    ledger = open_ledger(store, base_payload())
    settle(store, ledger["id"], 0)
    reloaded = SettlementStore(db_path).load(ledger["id"])
    assert reloaded["closed"] is True and len(reloaded["paid"]) == 8
    assert set(reloaded["paid"].values()) == {"甲", "乙"}
    # 落库 JSON 的像元键为 "x,y" 字符串。
    with sqlite3.connect(db_path) as db:
        raw = json.loads(db.execute("SELECT paid FROM ledgers").fetchone()[0])
    assert all("," in key for key in raw)
