"""像元结算裁决引擎（纯函数，不持有状态）。

结算规则：
- 瓦片是整数网格上的左闭右开矩形，按区域裁切后枚举有效像元。
- 同一像元只结算一次：已经付款的像元保留原领款人，不再参与重算。
- 未付款像元在所有覆盖瓦片中取验收等级最高者；最高等级由多个
  不同供应商同时持有时产生跨供应商争议，先挂起。
- 迟到瓦片等级再高也不能改写已付款像元的归属；未付款像元照新证据算。
- 关账时已付、可付、争议三个范围互不重叠，合起来必须正好等于有效覆盖，
  否则分区守恒失败，关不了账。
"""

from app.business import DomainIssue

# 单账有效像元上限，防止区域矩形过大导致枚举失控。
MAX_CELLS = 200_000
ID_LIMIT = 64


def _is_int(value):
    # 拒绝 bool，只接受真正的整数。
    return isinstance(value, int) and not isinstance(value, bool)


def _identifier(value):
    return (
        isinstance(value, str)
        and 0 < len(value.strip()) <= ID_LIMIT
        and value.strip() == value
    )


def validate_payload(payload):
    """校验结算输入，返回规范化后的 (region, tiles)。"""
    if not isinstance(payload, dict):
        raise DomainIssue("SETTLEMENT_PAYLOAD")
    region = payload.get("region")
    tiles = payload.get("tiles")
    if not isinstance(region, dict) or not isinstance(tiles, list) or not tiles:
        raise DomainIssue("SETTLEMENT_PAYLOAD")

    bounds = tuple(region.get(key) for key in ("x0", "y0", "x1", "y1"))
    if not all(_is_int(value) for value in bounds):
        raise DomainIssue("BAD_REGION")
    x0, y0, x1, y1 = bounds
    if x0 >= x1 or y0 >= y1 or x0 < 0 or y0 < 0:
        raise DomainIssue("BAD_REGION")
    if (x1 - x0) * (y1 - y0) > MAX_CELLS:
        raise DomainIssue("REGION_TOO_LARGE")

    normalized = []
    seen_ids = set()
    for tile in tiles:
        if not isinstance(tile, dict):
            raise DomainIssue("BAD_TILE")
        tile_id = tile.get("id")
        supplier = tile.get("supplier")
        grade = tile.get("grade")
        box = tuple(tile.get(key) for key in ("x0", "y0", "x1", "y1"))
        if not _identifier(tile_id) or not _identifier(supplier):
            raise DomainIssue("BAD_TILE")
        if tile_id in seen_ids:
            raise DomainIssue("TILE_ID_DUPLICATE")
        if not _is_int(grade) or grade < 0:
            raise DomainIssue("GRADE_INVALID")
        if not all(_is_int(value) for value in box):
            raise DomainIssue("BAD_TILE")
        a, b, c, d = box
        if a >= c or b >= d:
            raise DomainIssue("BAD_TILE")
        # 瓦片必须落在验收区域内；越界来源在进账前退回。
        if a < x0 or b < y0 or c > x1 or d > y1:
            raise DomainIssue("TILE_OUT_OF_REGION")
        seen_ids.add(tile_id)
        normalized.append(
            dict(id=tile_id, supplier=supplier, grade=grade, box=(a, b, c, d))
        )
    return (x0, y0, x1, y1), normalized


def enumerate_cells(region):
    """枚举区域内全部像元坐标，返回 (x, y) 迭代器。"""
    x0, y0, x1, y1 = region
    for y in range(y0, y1):
        for x in range(x0, x1):
            yield (x, y)


def _covers(tile, cell):
    x, y = cell
    a, b, c, d = tile["box"]
    return a <= x < c and b <= y < d


def adjudicate(cell, tiles, paid):
    """裁决单个像元，返回 (状态, 领款供应商或 None, 证据)。

    状态取值：PAID（已付款，保留原领款人）、PAYABLE（可付，唯一最高等级）、
    DISPUTED（同级跨供应商争议，挂起）。没有任何瓦片覆盖且从未付款的像元
    属于覆盖缺口，返回 GAP，由调用方排除在结算范围之外。
    """
    if cell in paid:
        owner = paid[cell]
        return "PAID", owner, {"owner": owner, "retained": True}

    claimants = [tile for tile in tiles if _covers(tile, cell)]
    if not claimants:
        return "GAP", None, {}
    top_grade = max(tile["grade"] for tile in claimants)
    winners = [tile for tile in claimants if tile["grade"] == top_grade]
    suppliers = sorted({tile["supplier"] for tile in winners})
    if len(suppliers) > 1:
        return (
            "DISPUTED",
            None,
            {
                "grade": top_grade,
                "suppliers": suppliers,
                "tiles": sorted(tile["id"] for tile in winners),
            },
        )
    owner = suppliers[0]
    return (
        "PAYABLE",
        owner,
        {
            "owner": owner,
            "grade": top_grade,
            "tiles": sorted(tile["id"] for tile in winners),
        },
    )


def close_ledger(payload, paid):
    """按当前证据与已付款账重算整张结算账。

    返回结构包含 paid/payable/disputed 三个像元明细桶、按供应商汇总的
    可付像元数，以及分区守恒校验结论。有效覆盖是"当前瓦片并集 ∪ 历史已付
    像元"——区域内无人覆盖的缺口不参与结算。
    """
    region, tiles = validate_payload(payload)
    if not isinstance(paid, dict):
        raise DomainIssue("SETTLEMENT_PAYLOAD")
    region_box = region
    for cell, owner in paid.items():
        if (
            not isinstance(cell, tuple)
            or len(cell) != 2
            or not all(_is_int(v) for v in cell)
            or not _identifier(owner)
        ):
            raise DomainIssue("PAID_LEDGER_INVALID")
        x, y = cell
        if not (region_box[0] <= x < region_box[2] and region_box[1] <= y < region_box[3]):
            raise DomainIssue("PAID_LEDGER_INVALID")

    buckets = {"paid": [], "payable": [], "disputed": []}
    totals = {"paid": 0, "payable": 0, "disputed": 0}
    supplier_payable = {}
    covered = set()

    for cell in enumerate_cells(region):
        status, owner, evidence = adjudicate(cell, tiles, paid)
        if status == "GAP":
            continue
        covered.add(cell)
        entry = {"cell": list(cell), **evidence}
        buckets[status.lower()].append(entry)
        totals[status.lower()] += 1
        if status == "PAYABLE":
            supplier_payable[owner] = supplier_payable.get(owner, 0) + 1

    # 已付款但当前证据已不再覆盖的像元仍属于有效覆盖（历史付款不能丢）。
    covered.update(paid.keys())
    valid_coverage = len(covered)
    region_cells = (region[2] - region[0]) * (region[3] - region[1])
    gap_cells = region_cells - valid_coverage
    bucket_total = sum(totals.values())
    covered_keys = set()
    for entries in buckets.values():
        for entry in entries:
            key = tuple(entry["cell"])
            if key in covered_keys:
                raise DomainIssue("PARTITION_BROKEN")
            covered_keys.add(key)
    # 分区守恒：三桶互不重叠且并集正好等于有效覆盖，否则关不了账。
    if bucket_total != valid_coverage or covered_keys != covered:
        raise DomainIssue("PARTITION_BROKEN")

    return {
        "region": dict(zip(("x0", "y0", "x1", "y1"), region)),
        "region_cells": region_cells,
        "valid_coverage": valid_coverage,
        "gap_cells": gap_cells,
        "totals": totals,
        "supplier_payable": dict(sorted(supplier_payable.items())),
        "buckets": buckets,
        "partition_ok": True,
    }


def payable_cells(snapshot):
    """从关账快照中提取当前可付像元集合（坐标元组）。"""
    return {tuple(entry["cell"]) for entry in snapshot["buckets"]["payable"]}
