import pytest

from app.main import create_app


@pytest.fixture
def client(tmp_path):
    return create_app(tmp_path / "runs.db", tmp_path / "settle.db").test_client()


def payload():
    return {
        "region": {"x0": 0, "y0": 0, "x1": 4, "y1": 2},
        "tiles": [
            {"id": "A1", "supplier": "甲", "grade": 3, "x0": 0, "y0": 0, "x1": 3, "y1": 2},
            {"id": "B1", "supplier": "乙", "grade": 3, "x0": 2, "y0": 0, "x1": 4, "y1": 2},
        ],
    }


def test_demo_file_served(client):
    response = client.get("/api/settlement/demo")
    assert response.status_code == 200
    assert len(response.json["scenarios"]) == 3


def test_open_preview_close_flow_and_partition(client):
    created = client.post("/api/settlement/ledgers", json=payload())
    assert created.status_code == 201
    ledger_id = created.json["id"]

    preview = client.post(f"/api/settlement/ledgers/{ledger_id}/preview")
    assert preview.status_code == 200
    assert preview.json["totals"] == {"paid": 0, "payable": 6, "disputed": 2}
    assert preview.json["valid_coverage"] == 8

    bad = client.post(
        f"/api/settlement/ledgers/{ledger_id}/close", json={"revision": 9}
    )
    assert bad.status_code == 409 and bad.json["code"] == "STALE_SETTLEMENT"

    closed = client.post(
        f"/api/settlement/ledgers/{ledger_id}/close", json={"revision": 0}
    )
    assert closed.status_code == 200 and closed.json["revision"] == 1
    assert closed.json["closed"] is True

    events = client.get(f"/api/settlement/ledgers/{ledger_id}/events")
    assert events.status_code == 200 and len(events.json["items"]) == 1


def test_open_is_idempotent(client):
    first = client.post("/api/settlement/ledgers", json=payload())
    second = client.post("/api/settlement/ledgers", json=payload())
    assert first.json["id"] == second.json["id"]


def test_closed_ledger_rejects_second_close(client):
    ledger_id = client.post("/api/settlement/ledgers", json=payload()).json["id"]
    client.post(f"/api/settlement/ledgers/{ledger_id}/close", json={"revision": 0})
    response = client.post(
        f"/api/settlement/ledgers/{ledger_id}/close", json={"revision": 1}
    )
    assert response.status_code == 409 and response.json["code"] == "LEDGER_FINALIZED"


def test_late_tile_retains_paid_owner(client):
    ledger_id = client.post("/api/settlement/ledgers", json=payload()).json["id"]
    client.post(f"/api/settlement/ledgers/{ledger_id}/close", json={"revision": 0})

    late = payload()
    late["tiles"].append(
        {"id": "C1", "supplier": "丙", "grade": 4, "x0": 0, "y0": 0, "x1": 4, "y1": 2}
    )
    reopened = client.post(f"/api/settlement/ledgers/{ledger_id}/late", json=late)
    assert reopened.status_code == 200 and reopened.json["closed"] is False

    snapshot = client.post(f"/api/settlement/ledgers/{ledger_id}/preview").json
    # 已付 6 像元保留原领款人；争议 2 像元被丙的高等级证据裁决为可付。
    assert snapshot["totals"] == {"paid": 6, "payable": 2, "disputed": 0}
    assert all(entry["owner"] == "丙" for entry in snapshot["buckets"]["payable"])


def test_validation_errors_are_422(client):
    bad = {"region": {"x0": 0, "y0": 0, "x1": 0, "y1": 2}, "tiles": payload()["tiles"]}
    response = client.post("/api/settlement/ledgers", json=bad)
    assert response.status_code == 422 and response.json["code"] == "BAD_REGION"


def test_unknown_ledger_is_404(client):
    response = client.get("/api/settlement/ledgers/ledger-missing")
    assert response.status_code == 404 and response.json["code"] == "LEDGER_NOT_FOUND"
