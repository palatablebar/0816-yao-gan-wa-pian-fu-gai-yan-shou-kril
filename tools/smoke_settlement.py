"""纯标准库冒烟脚本：在没有 pytest/flask 的环境下验证结算引擎与存储。

运行：python3 tools/smoke_settlement.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.business import DomainIssue  # noqa: E402
from app.settlement.engine import close_ledger  # noqa: E402
from app.settlement.service import (  # noqa: E402
    accept_late_tiles,
    open_ledger,
    preview,
    settle,
)
from app.settlement.store import SettlementStore  # noqa: E402

PASS = 0


def check(name, condition):
    global PASS
    assert condition, name
    PASS += 1
    print(f"  ok - {name}")


def tile(tid, supplier, grade, box):
    return dict(
        id=tid, supplier=supplier, grade=grade,
        **dict(zip(("x0", "y0", "x1", "y1"), box)),
    )


def main():
    # 规则 1+2：等级优先、同级争议挂起、三桶守恒。
    disputed = close_ledger(
        {
            "region": {"x0": 0, "y0": 0, "x1": 4, "y1": 2},
            "tiles": [
                tile("A1", "甲", 3, (0, 0, 3, 2)),
                tile("B1", "乙", 3, (2, 0, 4, 2)),
            ],
        },
        {},
    )
    check("争议挂起 2 像元", disputed["totals"] == {"paid": 0, "payable": 6, "disputed": 2})
    check("三桶合计等于有效覆盖", sum(disputed["totals"].values()) == disputed["valid_coverage"] == 8)

    # 规则 3：已付款保留原领款人，未付款按新证据。
    base = {
        "region": {"x0": 0, "y0": 0, "x1": 4, "y1": 2},
        "tiles": [
            tile("A1", "甲", 2, (0, 0, 3, 2)),
            tile("B1", "乙", 2, (3, 0, 4, 2)),
        ],
    }
    with tempfile.TemporaryDirectory() as tmp:
        store = SettlementStore(Path(tmp) / "s.db")
        ledger = open_ledger(store, base)
        check("建账 revision=0", ledger["revision"] == 0 and ledger["closed"] is False)
        closed = settle(store, ledger["id"], 0)
        check(
            "首次关账 8 像元可付并入账",
            closed["snapshot"]["totals"] == {"paid": 0, "payable": 8, "disputed": 0}
            and len(closed["paid"]) == 8,
        )

        late = dict(base)
        late["tiles"] = [
            *base["tiles"],
            tile("C1", "丙", 4, (1, 0, 4, 2)),
        ]
        accept_late_tiles(store, ledger["id"], late)
        snap = preview(store, ledger["id"])
        check("迟到高等级不改写已付款", snap["totals"] == {"paid": 8, "payable": 0, "disputed": 0})
        check("原领款人保留", {e["owner"] for e in snap["buckets"]["paid"]} == {"甲", "乙"})

        # 规则 4：过期 revision 关账被退回。
        try:
            settle(store, ledger["id"], 0)
        except DomainIssue as error:
            check("过期关账返回 STALE_SETTLEMENT", error.code == "STALE_SETTLEMENT")
        else:
            raise AssertionError("应拒绝过期关账")

        again = settle(store, ledger["id"], 1)
        check("新 revision 关账成功", again["revision"] == 2 and again["closed"] is True)
        check("关账事件审计一条次", len(store.list_events(ledger["id"])) == 2)

    # 缺口像元不进结算桶，但守恒仍成立。
    gap = close_ledger(
        {
            "region": {"x0": 0, "y0": 0, "x1": 4, "y1": 1},
            "tiles": [tile("A1", "甲", 1, (0, 0, 2, 1))],
        },
        {},
    )
    check(
        "缺口 2 像元不结算",
        gap["gap_cells"] == 2
        and gap["valid_coverage"] == 2
        and gap["totals"] == {"paid": 0, "payable": 2, "disputed": 0},
    )
    # 规则 5：注入重复像元破坏互不重叠，拒绝关账。
    from app.settlement import engine

    original = engine.enumerate_cells

    def duplicated(region):
        yield from original(region)
        yield (region[0], region[1])

    engine.enumerate_cells = duplicated
    try:
        try:
            close_ledger(
                {"region": {"x0": 0, "y0": 0, "x1": 3, "y1": 1},
                 "tiles": [tile("A1", "甲", 1, (0, 0, 3, 1))]},
                {},
            )
        except DomainIssue as error:
            check("守恒失败拒绝关账", error.code == "PARTITION_BROKEN")
        else:
            raise AssertionError("应拒绝守恒失败的关账")
    finally:
        engine.enumerate_cells = original

    print(f"\n全部 {PASS} 项冒烟检查通过")


if __name__ == "__main__":
    main()
