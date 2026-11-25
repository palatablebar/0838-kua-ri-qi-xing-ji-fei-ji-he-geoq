from flask import Blueprint, current_app, jsonify, request

from app.refunds import service


api = Blueprint("refunds", __name__)


def store():
    return current_app.extensions["refunds"]


@api.post("/api/refunds/batches")
def book_batch():
    return jsonify(service.book_batch(store(), request.get_json(silent=True) or {}))


@api.post("/api/refunds/vouchers")
def receive_vouchers():
    return jsonify(service.receive_vouchers(store(), request.get_json(silent=True) or {}))


@api.post("/api/refunds/batches/<batch_id>/issue")
def issue_batch(batch_id):
    return jsonify(service.issue(store(), batch_id))


@api.get("/api/refunds/workbench")
def board():
    return jsonify(service.workbench(store()))


@api.get("/api/refunds/sample")
def sample():
    return jsonify(service.sample())
