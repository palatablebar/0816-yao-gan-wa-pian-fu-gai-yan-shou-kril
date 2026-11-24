"""用最小 Fake Flask 演练结算 HTTP 蓝图（环境无 flask 时使用）。"""

import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class FakeBlueprint:
    def __init__(self, name, import_name, url_prefix=""):
        self.name = name
        self.url_prefix = url_prefix
        self.routes = {}

    def _register(self, rule, methods):
        def wrap(func):
            for method in methods:
                self.routes[(method, rule)] = func
            return func

        return wrap

    def post(self, rule):
        return self._register(rule, ["POST"])

    def get(self, rule):
        return self._register(rule, ["GET"])

    route = None


flask = types.ModuleType("flask")
flask.Blueprint = FakeBlueprint
flask.jsonify = lambda payload=None, **kw: ("json", payload if payload is not None else kw)


class FakeRequest:
    def __init__(self):
        self._json = None

    def get_json(self, silent=False):
        return self._json


flask.request = FakeRequest()
flask.current_app = types.SimpleNamespace()


def fake_jsonify(payload=None, **kw):
    return payload if payload is not None else kw


def fake_send(directory, filename, mimetype=None):
    return ("file", str(Path(directory) / filename), mimetype)


flask.send_from_directory = fake_send
flask.jsonify = fake_jsonify
sys.modules["flask"] = flask

# app.http_errors 只依赖 flask.jsonify。
from app.settlement.api import settlement_api  # noqa: E402
from app.settlement.service import open_ledger  # noqa: E402
from app.settlement.store import SettlementStore  # noqa: E402
from app.business import DomainIssue  # noqa: E402

PASS = 0


def check(name, condition):
    global PASS
    assert condition, name
    PASS += 1
    print(f"  ok - {name}")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        store = SettlementStore(Path(tmp) / "s.db")
        flask.current_app.extensions = {"settlements": store}

        payload = {
            "region": {"x0": 0, "y0": 0, "x1": 4, "y1": 2},
            "tiles": [
                {"id": "A1", "supplier": "甲", "grade": 3, "x0": 0, "y0": 0, "x1": 3, "y1": 2},
                {"id": "B1", "supplier": "乙", "grade": 3, "x0": 2, "y0": 0, "x1": 4, "y1": 2},
            ],
        }

        # demo
        result = settlement_api.routes[("GET", "/demo")]()
        check("demo 路由返回文件", result[0] == "file" and result[1].endswith("settlement_demo.json"))

        # create ledger
        flask.request._json = payload
        body, code = settlement_api.routes[("POST", "/ledgers")]()
        check("建账返回 201 语义", code == 201 and body["revision"] == 0 and body["closed"] is False)
        ledger_id = body["id"]

        # preview
        snap = settlement_api.routes[("POST", "/ledgers/<ledger_id>/preview")](ledger_id)
        check("试算争议 2、可付 6", snap["totals"] == {"paid": 0, "payable": 6, "disputed": 2})

        # stale close
        flask.request._json = {"revision": 9}
        try:
            settlement_api.routes[("POST", "/ledgers/<ledger_id>/close")](ledger_id)
        except DomainIssue as error:
            check("过期 revision 拒绝", error.code == "STALE_SETTLEMENT")

        # valid close
        flask.request._json = {"revision": 0}
        closed = settlement_api.routes[("POST", "/ledgers/<ledger_id>/close")](ledger_id)
        check("关账成功 revision=1", closed["revision"] == 1 and closed["closed"] is True)

        # second close rejected
        flask.request._json = {"revision": 1}
        try:
            settlement_api.routes[("POST", "/ledgers/<ledger_id>/close")](ledger_id)
        except DomainIssue as error:
            check("重复关账拒绝", error.code == "LEDGER_FINALIZED")

        # late tiles
        late = dict(payload)
        late["tiles"] = [
            *payload["tiles"],
            {"id": "C1", "supplier": "丙", "grade": 4, "x0": 0, "y0": 0, "x1": 4, "y1": 2},
        ]
        flask.request._json = late
        reopened = settlement_api.routes[("POST", "/ledgers/<ledger_id>/late")](ledger_id)
        check("迟到证据重开", reopened["closed"] is False and reopened["revision"] == 1)
        snap2 = settlement_api.routes[("POST", "/ledgers/<ledger_id>/preview")](ledger_id)
        check(
            "已付 6 保留、争议 2 被高等级裁决为可付",
            snap2["totals"] == {"paid": 6, "payable": 2, "disputed": 0},
        )

        # events
        events = settlement_api.routes[("GET", "/ledgers/<ledger_id>/events")](ledger_id)
        check("关账事件审计 1 条", len(events["items"]) == 1)

        # 404
        try:
            settlement_api.routes[("GET", "/ledgers/<ledger_id>")]("ledger-missing")
        except DomainIssue as error:
            check("未知账 404 语义", error.code == "LEDGER_NOT_FOUND")

        # malformed body
        flask.request._json = None
        try:
            settlement_api.routes[("POST", "/ledgers")]()
        except DomainIssue as error:
            check("非法请求体", error.code == "SETTLEMENT_PAYLOAD")

    print(f"\n全部 {PASS} 项 HTTP 蓝图演练通过")


if __name__ == "__main__":
    main()
