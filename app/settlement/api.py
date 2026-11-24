"""像元结算账 HTTP 接口。"""

from flask import Blueprint, current_app, jsonify, request, send_from_directory

from app.business import DomainIssue
from app.config import ROOT, SETTLEMENT_DEMO
from app.settlement.service import (
    accept_late_tiles,
    open_ledger,
    preview,
    public_view,
    settle,
)

settlement_api = Blueprint("settlement", __name__, url_prefix="/api/settlement")


def _store():
    return current_app.extensions["settlements"]


def _json_body():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise DomainIssue("SETTLEMENT_PAYLOAD")
    return payload


@settlement_api.get("/demo")
def demo():
    return send_from_directory(
        ROOT / "sample_data", SETTLEMENT_DEMO, mimetype="application/json"
    )


@settlement_api.post("/ledgers")
def create_ledger():
    record = open_ledger(_store(), _json_body())
    return jsonify(public_view(record)), 201


@settlement_api.get("/ledgers/<ledger_id>")
def get_ledger(ledger_id):
    return jsonify(public_view(_store().load(ledger_id)))


@settlement_api.post("/ledgers/<ledger_id>/preview")
def preview_ledger(ledger_id):
    return jsonify(preview(_store(), ledger_id))


@settlement_api.post("/ledgers/<ledger_id>/close")
def close_ledger_route(ledger_id):
    body = _json_body()
    revision = body.get("revision")
    if not isinstance(revision, int):
        raise DomainIssue("SETTLEMENT_PAYLOAD")
    record = settle(_store(), ledger_id, revision)
    return jsonify(public_view(record))


@settlement_api.post("/ledgers/<ledger_id>/late")
def late_route(ledger_id):
    record = accept_late_tiles(_store(), ledger_id, _json_body())
    return jsonify(public_view(record))


@settlement_api.get("/ledgers/<ledger_id>/events")
def events_route(ledger_id):
    _store().load(ledger_id)
    return jsonify(items=_store().list_events(ledger_id))
