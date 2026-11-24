"""结算账服务：把纯裁决引擎与持久化、审计串起来。"""

from datetime import datetime, timezone
from hashlib import sha256

from app.business import DomainIssue
from app.config import SETTLEMENT_RULE_VERSION
from app.settlement.engine import close_ledger, validate_payload
from app.settlement.store import SettlementStore  # noqa: F401  便于外部导入


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _ledger_id(payload):
    digest = sha256(
        f"{SETTLEMENT_RULE_VERSION}:{payload}".encode("utf-8", "replace")
    ).hexdigest()
    return "ledger-" + digest[:16]


def public_view(record):
    """对外视图：隐藏存储内部字段，只暴露账的当前结论。"""
    return {
        "id": record["id"],
        "revision": record["revision"],
        "closed": record["closed"],
        "updated": record["updated"],
        "snapshot": record["snapshot"],
    }


def open_ledger(store, payload):
    """建账或载入同一证据的既有账（幂等，不覆盖已付款历史）。"""
    validate_payload(payload)
    ledger_id = _ledger_id(payload)
    try:
        return store.load(ledger_id)
    except DomainIssue as error:
        if error.code != "LEDGER_NOT_FOUND":
            raise
    return store.create(ledger_id, payload, now())


def settle(store, ledger_id, revision):
    """按当前证据与已付款账重算并关账。

    已付款像元保留原领款人；未付款像元照新证据裁决。返回的内部快照
    附带 `_paid` 供存储层编码，落库前会被剥离。
    """
    record = store.load(ledger_id)
    if record["closed"]:
        raise DomainIssue("LEDGER_FINALIZED")
    if revision != record["revision"]:
        raise DomainIssue("STALE_SETTLEMENT")
    snapshot = close_ledger(record["payload"], record["paid"])
    # 已付款明细直接来自既有账，关账后继续保留原领款人。
    snapshot["_paid"] = dict(record["paid"])
    return store.close(ledger_id, revision, snapshot, now())


def accept_late_tiles(store, ledger_id, payload):
    """迟到瓦片进账：更新证据并重开账，已付款范围原样保留。"""
    validate_payload(payload)
    record = store.load(ledger_id)
    # 区域坐标是付款坐标的锚点，迟到证据不允许换区域。
    if payload["region"] != record["payload"]["region"]:
        raise DomainIssue("REGION_MOVED")
    if not record["closed"]:
        raise DomainIssue("LEDGER_NOT_CLOSED")
    return store.update_payload(ledger_id, payload, now())


def preview(store, ledger_id):
    """不落库的试算：展示当前证据下已付/可付/争议将如何结算。"""
    record = store.load(ledger_id)
    snapshot = close_ledger(record["payload"], record["paid"])
    snapshot["revision"] = record["revision"]
    snapshot["closed"] = record["closed"]
    return snapshot
