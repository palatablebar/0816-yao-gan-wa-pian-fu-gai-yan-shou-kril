"""结算账的 SQLite 持久化与乐观并发控制。

两名操作员同时关账时，只有持有当前 revision 的一方能提交，另一方拿到
STALE_SETTLEMENT，必须按最新已付款账重新计算后再关账——过期的结算结果
一律退回重算，不允许覆盖。
"""

import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

from app.business import DomainIssue

SCHEMA = """
CREATE TABLE IF NOT EXISTS ledgers (
    id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    paid TEXT NOT NULL DEFAULT '{}',
    snapshot TEXT,
    revision INTEGER NOT NULL DEFAULT 0,
    closed INTEGER NOT NULL DEFAULT 0,
    created TEXT NOT NULL,
    updated TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS close_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ledger_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    totals TEXT NOT NULL,
    created TEXT NOT NULL
);
"""


def _dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class SettlementStore:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=15)) as db, db:
            db.row_factory = sqlite3.Row
            db.executescript(SCHEMA)
            yield db

    def create(self, ledger_id, payload, now):
        with self.connect() as db:
            db.execute(
                "INSERT INTO ledgers(id,payload,created,updated) VALUES (?,?,?,?)",
                (ledger_id, _dumps(payload), now, now),
            )
        return self.load(ledger_id)

    def load(self, ledger_id):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM ledgers WHERE id=?", (ledger_id,)
            ).fetchone()
        if row is None:
            raise DomainIssue("LEDGER_NOT_FOUND")
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        # JSON 对象键是 "x,y" 字符串，转回像元坐标元组供引擎使用。
        result["paid"] = {
            tuple(int(part) for part in key.split(",")): owner
            for key, owner in json.loads(row["paid"]).items()
        }
        result["snapshot"] = json.loads(row["snapshot"]) if row["snapshot"] else None
        result["closed"] = bool(row["closed"])
        return result

    def list_events(self, ledger_id):
        with self.connect() as db:
            rows = db.execute(
                "SELECT revision,totals,created FROM close_events "
                "WHERE ledger_id=? ORDER BY id",
                (ledger_id,),
            ).fetchall()
        return [
            dict(revision=row["revision"], totals=json.loads(row["totals"]), created=row["created"])
            for row in rows
        ]

    def close(self, ledger_id, revision, snapshot, now):
        """按 revision 乐观关账；revision 过期或账已关闭都退回重算。

        关账把本次裁决的可付像元按领款人转为已付，连同历史已付款一起入账；
        历史已付款的归属在引擎阶段已保留，这里原样持久化。
        """
        paid_after = dict(snapshot["_paid"])
        for entry in snapshot["buckets"]["payable"]:
            paid_after[tuple(entry["cell"])] = entry["owner"]
        paid_encoded = _dumps(
            {f"{cell[0]},{cell[1]}": owner for cell, owner in paid_after.items()}
        )
        snapshot_public = {k: v for k, v in snapshot.items() if not k.startswith("_")}
        with self.connect() as db:
            current = db.execute(
                "SELECT revision,closed FROM ledgers WHERE id=?", (ledger_id,)
            ).fetchone()
            if current is None:
                raise DomainIssue("LEDGER_NOT_FOUND")
            if current["closed"]:
                raise DomainIssue("LEDGER_FINALIZED")
            if current["revision"] != revision:
                raise DomainIssue("STALE_SETTLEMENT")
            cursor = db.execute(
                "UPDATE ledgers SET paid=?, snapshot=?, revision=revision+1, "
                "closed=1, updated=? WHERE id=? AND revision=? AND closed=0",
                (paid_encoded, _dumps(snapshot_public), now, ledger_id, revision),
            )
            if cursor.rowcount != 1:
                raise DomainIssue("STALE_SETTLEMENT")
            db.execute(
                "INSERT INTO close_events(ledger_id,revision,totals,created) "
                "VALUES (?,?,?,?)",
                (
                    ledger_id,
                    revision + 1,
                    _dumps(snapshot_public["totals"]),
                    now,
                ),
            )
        return self.load(ledger_id)

    def update_payload(self, ledger_id, payload, now):
        with self.connect() as db:
            cursor = db.execute(
                "UPDATE ledgers SET payload=?, closed=0, snapshot=NULL, updated=? "
                "WHERE id=?",
                (_dumps(payload), now, ledger_id),
            )
            if cursor.rowcount != 1:
                raise DomainIssue("LEDGER_NOT_FOUND")
        return self.load(ledger_id)
