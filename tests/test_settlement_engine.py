import pytest

from app.business import DomainIssue
from app.settlement.engine import close_ledger, validate_payload

REGION = {"x0": 0, "y0": 0, "x1": 4, "y1": 3}


def tile(tid, supplier, grade, box):
    return dict(id=tid, supplier=supplier, grade=grade, **dict(zip(("x0", "y0", "x1", "y1"), box)))


def payload(tiles, region=REGION):
    return {"region": region, "tiles": tiles}


def test_single_supplier_covers_all_cells_payable():
    result = close_ledger(
        payload([tile("A1", "甲", 1, (0, 0, 4, 3))]), paid={}
    )
    assert result["totals"] == {"paid": 0, "payable": 12, "disputed": 0}
    assert result["supplier_payable"] == {"甲": 12}
    assert result["partition_ok"] is True


def test_higher_grade_wins_overlap_cell():
    # A 等级 2 覆盖左 3 列，B 等级 3 覆盖右 2 列全高；重叠 1 列归高等级的 B。
    result = close_ledger(
        payload(
            [
                tile("A1", "甲", 2, (0, 0, 3, 3)),
                tile("B1", "乙", 3, (2, 0, 4, 3)),
            ]
        ),
        paid={},
    )
    assert result["gap_cells"] == 0
    assert result["totals"] == {"paid": 0, "payable": 12, "disputed": 0}
    assert result["supplier_payable"] == {"甲": 6, "乙": 6}


def test_same_grade_different_suppliers_suspends_dispute():
    result = close_ledger(
        payload(
            [
                tile("A1", "甲", 3, (0, 0, 3, 2)),
                tile("B1", "乙", 3, (2, 0, 4, 2)),
            ],
            region={"x0": 0, "y0": 0, "x1": 4, "y1": 2},
        ),
        paid={},
    )
    assert result["totals"] == {"paid": 0, "payable": 6, "disputed": 2}
    disputed = result["buckets"]["disputed"]
    assert {tuple(e["cell"]) for e in disputed} == {(2, 0), (2, 1)}
    assert all(e["suppliers"] == ["甲", "乙"] and e["grade"] == 3 for e in disputed)


def test_same_supplier_same_grade_does_not_dispute():
    # 同一供应商的两张同级瓦片覆盖同一像元，不构成跨供应商争议。
    result = close_ledger(
        payload(
            [
                tile("A1", "甲", 2, (0, 0, 2, 1)),
                tile("A2", "甲", 2, (1, 0, 3, 1)),
            ],
            region={"x0": 0, "y0": 0, "x1": 3, "y1": 1},
        ),
        paid={},
    )
    assert result["totals"] == {"paid": 0, "payable": 3, "disputed": 0}


def test_paid_cell_keeps_original_owner_even_against_higher_grade():
    # 已付款给甲的 (0,0)，即使乙拿出更高等级瓦片也不改写归属。
    result = close_ledger(
        payload(
            [
                tile("A1", "甲", 1, (0, 0, 2, 1)),
                tile("B1", "乙", 4, (0, 0, 2, 1)),
            ],
            region={"x0": 0, "y0": 0, "x1": 2, "y1": 1},
        ),
        paid={(0, 0): "甲"},
    )
    assert result["totals"] == {"paid": 1, "payable": 1, "disputed": 0}
    paid_entry = result["buckets"]["paid"][0]
    assert paid_entry["cell"] == [0, 0] and paid_entry["owner"] == "甲"
    assert paid_entry["retained"] is True
    assert result["buckets"]["payable"][0]["owner"] == "乙"


def test_unpaid_cell_follows_new_high_grade_evidence():
    # 迟到的丙等级 4 覆盖未付款像元，未付款部分按新证据归丙。
    late = payload(
        [
            tile("A1", "甲", 2, (0, 0, 3, 2)),
            tile("B1", "乙", 2, (3, 0, 4, 2)),
            tile("C1", "丙", 4, (1, 0, 4, 2)),
        ],
        region={"x0": 0, "y0": 0, "x1": 4, "y1": 2},
    )
    paid = {(x, y): ("乙" if x == 3 else "甲") for y in range(2) for x in range(4)}
    result = close_ledger(late, paid)
    assert result["totals"] == {"paid": 8, "payable": 0, "disputed": 0}

    # 去掉部分已付款：剩余未付款像元照新证据归丙。
    partial = {cell: owner for cell, owner in paid.items() if cell[0] in (0, 3)}
    result = close_ledger(late, partial)
    assert result["totals"] == {"paid": 4, "payable": 4, "disputed": 0}
    assert all(entry["owner"] == "丙" for entry in result["buckets"]["payable"])


def test_late_tile_turns_payable_into_dispute_when_tied():
    # 甲等级 3 独占区域；迟到同级乙覆盖其中一列，未付款的该列变争议。
    base = payload(
        [tile("A1", "甲", 3, (0, 0, 3, 1))],
        region={"x0": 0, "y0": 0, "x1": 3, "y1": 1},
    )
    first = close_ledger(base, {})
    assert first["totals"]["payable"] == 3
    late = payload(
        [
            tile("A1", "甲", 3, (0, 0, 3, 1)),
            tile("B1", "乙", 3, (1, 0, 2, 1)),
        ],
        region=base["region"],
    )
    result = close_ledger(late, {(0, 0): "甲"})
    assert result["totals"] == {"paid": 1, "payable": 1, "disputed": 1}


def test_partition_buckets_are_disjoint_and_equal_coverage():
    result = close_ledger(
        payload(
            [
                tile("A1", "甲", 3, (0, 0, 3, 2)),
                tile("B1", "乙", 3, (2, 0, 4, 2)),
            ],
            region={"x0": 0, "y0": 0, "x1": 4, "y1": 2},
        ),
        paid={(0, 0): "甲"},
    )
    keys = set()
    for bucket in ("paid", "payable", "disputed"):
        for entry in result["buckets"][bucket]:
            keys.add(tuple(entry["cell"]))
    assert len(keys) == result["valid_coverage"] == 8
    assert sum(result["totals"].values()) == 8


def test_partition_broken_raises(monkeypatch):
    # 注入重复像元：同一像元进入两个桶，互不重叠约束被破坏，必须拒绝关账。
    from app.settlement import engine

    def duplicated_enumerate(region):
        yield from engine.enumerate_cells(region)
        yield (region[0], region[1])  # 重复首像元

    monkeypatch.setattr(engine, "enumerate_cells", duplicated_enumerate)
    with pytest.raises(DomainIssue, match="PARTITION_BROKEN"):
        close_ledger(payload([tile("A1", "甲", 1, (0, 0, 4, 1))]), {})


def test_historical_paid_owner_is_retained_even_if_supplier_gone():
    # 供应商撤场后，历史已付款像元仍保留原领款人，不因当前供应商名单而失效。
    result = close_ledger(
        payload([tile("A1", "甲", 1, (0, 0, 2, 1))], region={"x0": 0, "y0": 0, "x1": 2, "y1": 1}),
        paid={(0, 0): "原供应商"},
    )
    assert result["buckets"]["paid"][0]["owner"] == "原供应商"
    assert result["totals"]["paid"] == 1


def test_paid_ledger_rejects_malformed_owner_and_outside_cell():
    with pytest.raises(DomainIssue, match="PAID_LEDGER_INVALID"):
        close_ledger(
            payload([tile("A1", "甲", 1, (0, 0, 1, 1))], region={"x0": 0, "y0": 0, "x1": 1, "y1": 1}),
            paid={(0, 0): " 带空格 "},
        )
    with pytest.raises(DomainIssue, match="PAID_LEDGER_INVALID"):
        close_ledger(
            payload([tile("A1", "甲", 1, (0, 0, 1, 1))], region={"x0": 0, "y0": 0, "x1": 1, "y1": 1}),
            paid={(5, 5): "甲"},
        )


def test_uncovered_region_cells_are_gaps_not_settled():
    # 瓦片只覆盖左 2 列（2×2=4 像元），区域右 2 列是缺口，不进任何结算桶。
    result = close_ledger(
        payload([tile("A1", "甲", 1, (0, 0, 2, 2))], region={"x0": 0, "y0": 0, "x1": 4, "y1": 2}),
        paid={},
    )
    assert result["region_cells"] == 8
    assert result["gap_cells"] == 4
    assert result["valid_coverage"] == 4
    assert result["totals"] == {"paid": 0, "payable": 4, "disputed": 0}
    assert sum(result["totals"].values()) == result["valid_coverage"]


def test_paid_cell_without_current_coverage_still_counts_as_coverage():
    # 已付款像元即使当前没有任何瓦片覆盖，仍是有效覆盖，保留原领款人。
    result = close_ledger(
        payload([tile("A1", "甲", 1, (0, 0, 1, 1))], region={"x0": 0, "y0": 0, "x1": 2, "y1": 1}),
        paid={(1, 0): "甲"},
    )
    assert result["valid_coverage"] == 2 and result["gap_cells"] == 0
    assert result["totals"]["paid"] == 1


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"region": REGION},
        {"region": REGION, "tiles": []},
        {"region": "x", "tiles": []},
    ],
)
def test_malformed_payload_rejected(bad):
    with pytest.raises(DomainIssue, match="SETTLEMENT_PAYLOAD"):
        validate_payload(bad)


def test_invalid_region_and_bounds():
    with pytest.raises(DomainIssue, match="BAD_REGION"):
        validate_payload(payload([tile("A1", "甲", 1, (0, 0, 1, 1))], region={"x0": 2, "y0": 0, "x1": 2, "y1": 1}))
    with pytest.raises(DomainIssue, match="BAD_TILE"):
        validate_payload({"region": REGION, "tiles": [dict(id="A1", supplier="甲", grade=1, x0=1, y0=0, x1=1, y1=1)]})


def test_grade_must_be_non_negative_int_and_tile_id_unique():
    with pytest.raises(DomainIssue, match="GRADE_INVALID"):
        validate_payload(payload([tile("A1", "甲", -1, (0, 0, 1, 1))]))
    with pytest.raises(DomainIssue, match="TILE_ID_DUPLICATE"):
        validate_payload(
            payload(
                [
                    tile("A1", "甲", 1, (0, 0, 1, 1)),
                    tile("A1", "乙", 1, (1, 0, 2, 1)),
                ]
            )
        )


def test_tile_out_of_region_rejected():
    with pytest.raises(DomainIssue, match="TILE_OUT_OF_REGION"):
        validate_payload(payload([tile("A1", "甲", 1, (3, 0, 5, 1))]))


def test_region_cell_cap_enforced():
    huge = {"x0": 0, "y0": 0, "x1": 1000, "y1": 1000}
    with pytest.raises(DomainIssue, match="REGION_TOO_LARGE"):
        validate_payload(payload([tile("A1", "甲", 1, (0, 0, 1, 1))], region=huge))
