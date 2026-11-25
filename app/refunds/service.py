import json
from datetime import date

from app.config import SAMPLES
from app.errors import DomainIssue
from app.intake.normalize import canonical
from app.refunds.store import CLAIMED, PENDING


def _text(value, field):
    text = canonical(value)
    if not text:
        raise DomainIssue("REFUND_BAD_FIELD", detail=f"{field} 不能为空")
    return text


def _cents(value, field):
    if isinstance(value, bool) or not isinstance(value, int):
        raise DomainIssue("REFUND_BAD_AMOUNT", detail=f"{field} 必须为整数分")
    if value <= 0:
        raise DomainIssue("REFUND_BAD_AMOUNT", detail=f"{field} 必须为正整数分")
    return value


def _day(value, field):
    text = _text(value, field)
    try:
        date.fromisoformat(text)
    except ValueError:
        raise DomainIssue("REFUND_BAD_DATE", detail=f"{field} 须为 ISO 日期")
    return text


def _refund(item, index):
    if not isinstance(item, dict):
        raise DomainIssue("REFUND_BAD_FIELD", detail=f"refunds[{index}] 须为对象")
    return {"refund_id": _text(item.get("refund_id"), f"refunds[{index}].refund_id"),
            "amount_cents": _cents(item.get("amount_cents"), f"refunds[{index}].amount_cents"),
            "voucher_ref": canonical(item.get("voucher_ref"))}


def _voucher(item, index, default_on):
    if not isinstance(item, dict):
        raise DomainIssue("REFUND_BAD_FIELD", detail=f"vouchers[{index}] 须为对象")
    received = item.get("received_on") or default_on
    return {"voucher_id": _text(item.get("voucher_id"), f"vouchers[{index}].voucher_id"),
            "ride_id": _text(item.get("ride_id"), f"vouchers[{index}].ride_id"),
            "charged_cents": _cents(item.get("charged_cents"), f"vouchers[{index}].charged_cents"),
            "received_on": _day(received, f"vouchers[{index}].received_on")}


def _settlement(store, paid_on):
    rows = [row for row in store.entries() if row["paid_on"] == paid_on]
    claimed = sum(row["amount_cents"] for row in rows if row["status"] == CLAIMED)
    pending = sum(row["amount_cents"] for row in rows if row["status"] == PENDING)
    return {"paid_on": paid_on, "entries": len(rows), "paid_cents": claimed + pending,
            "claimed_cents": claimed, "pending_cents": pending}


def book_batch(store, payload):
    if not isinstance(payload, dict):
        raise DomainIssue("BAD_REQUEST")
    batch_id = _text(payload.get("batch_id"), "batch_id")
    paid_on = _day(payload.get("paid_on"), "paid_on")
    raw_refunds = payload.get("refunds")
    if not isinstance(raw_refunds, list) or not raw_refunds:
        raise DomainIssue("REFUND_BAD_FIELD", detail="refunds 须为非空数组")
    refunds = [_refund(item, index) for index, item in enumerate(raw_refunds)]
    if len({item["refund_id"] for item in refunds}) != len(refunds):
        raise DomainIssue("REFUND_BAD_FIELD", detail="refunds 内 refund_id 重复")
    raw_vouchers = payload.get("vouchers") or []
    if not isinstance(raw_vouchers, list):
        raise DomainIssue("REFUND_BAD_FIELD", detail="vouchers 须为数组")
    vouchers = [_voucher(item, index, paid_on) for index, item in enumerate(raw_vouchers)]
    digest_payload = {"paid_on": paid_on, "refunds": refunds, "vouchers": vouchers}
    digest = store.digest_of(digest_payload)
    existing = store.get_batch(batch_id)
    if existing and existing["digest"] != digest:
        raise DomainIssue("BATCH_CONFLICT", 409)
    result = store.book(batch_id, paid_on, refunds, vouchers, digest)
    batch = store.get_batch(batch_id)
    duplicates = sum(1 for item in result["entries"] if item["result"] == "duplicate")
    return {"batch": {"batch_id": batch_id, "paid_on": paid_on, "status": batch["status"],
                      "entries": batch["entries"]},
            "entries": result["entries"], "vouchers": result["vouchers"],
            "booked": len(result["entries"]) - duplicates, "duplicates": duplicates,
            "settlement": _settlement(store, paid_on)}


def receive_vouchers(store, payload):
    if not isinstance(payload, dict):
        raise DomainIssue("BAD_REQUEST")
    raw_vouchers = payload.get("vouchers")
    if not isinstance(raw_vouchers, list) or not raw_vouchers:
        raise DomainIssue("REFUND_BAD_FIELD", detail="vouchers 须为非空数组")
    today = date.today().isoformat()
    vouchers = [_voucher(item, index, today) for index, item in enumerate(raw_vouchers)]
    if len({item["voucher_id"] for item in vouchers}) != len(vouchers):
        raise DomainIssue("REFUND_BAD_FIELD", detail="vouchers 内 voucher_id 重复")
    results = store.receive(vouchers)
    claimed = [refund for item in results for refund in item["claimed"]]
    return {"vouchers": results, "claimed": claimed, "claimed_total": len(claimed)}


def issue(store, batch_id):
    batch, problem = store.issue(canonical(batch_id))
    if problem == "missing":
        raise DomainIssue("BATCH_UNKNOWN", 404)
    if problem == "issued":
        raise DomainIssue("BATCH_ALREADY_ISSUED", 409)
    return {"batch_id": batch["batch_id"], "paid_on": batch["paid_on"], "status": batch["status"],
            "issued_at": batch["issued_at"]}


def workbench(store):
    entries = store.entries()
    vouchers = store.vouchers()
    batches = store.batches()
    claimed_by_voucher = {}
    for row in entries:
        if row["status"] == CLAIMED:
            claimed_by_voucher[row["voucher_ref"]] = claimed_by_voucher.get(row["voucher_ref"], 0) + row["amount_cents"]
    days = []
    for paid_on in sorted({row["paid_on"] for row in entries}):
        rows = [row for row in entries if row["paid_on"] == paid_on]
        claimed = sum(row["amount_cents"] for row in rows if row["status"] == CLAIMED)
        pending = sum(row["amount_cents"] for row in rows if row["status"] == PENDING)
        days.append({"paid_on": paid_on, "entries": len(rows), "paid_cents": claimed + pending,
                     "claimed_cents": claimed, "pending_cents": pending,
                     "pending_ids": [row["refund_id"] for row in rows if row["status"] == PENDING]})
    voucher_rows = []
    for voucher in vouchers:
        claimed = claimed_by_voucher.get(voucher["voucher_id"], 0)
        voucher_rows.append({"voucher_id": voucher["voucher_id"], "ride_id": voucher["ride_id"],
                             "charged_cents": voucher["charged_cents"], "claimed_cents": claimed,
                             "remaining_cents": voucher["charged_cents"] - claimed,
                             "received_on": voucher["received_on"]})
    known = {voucher["voucher_id"]: voucher for voucher in vouchers}
    checks = [
        {"id": "no_over_refund", "label": "累计认退不超实扣",
         "ok": all(row["claimed_cents"] <= row["charged_cents"] for row in voucher_rows)},
        {"id": "claimed_has_voucher", "label": "已退必有原扣款凭据",
         "ok": all(row["voucher_ref"] in known for row in entries if row["status"] == CLAIMED)},
        {"id": "pending_not_claimable", "label": "暂挂无可认领滞留",
         "ok": all(not (row["voucher_ref"] in known and
                        known[row["voucher_ref"]]["charged_cents"] -
                        claimed_by_voucher.get(row["voucher_ref"], 0) >= row["amount_cents"])
                   for row in entries if row["status"] == PENDING)},
        {"id": "day_balanced", "label": "当日出款全部进当日结算",
         "ok": all(day["paid_cents"] == day["claimed_cents"] + day["pending_cents"] for day in days)},
    ]
    totals = {"entries": len(entries),
              "claimed": sum(1 for row in entries if row["status"] == CLAIMED),
              "pending": sum(1 for row in entries if row["status"] == PENDING),
              "paid_cents": sum(row["amount_cents"] for row in entries),
              "claimed_cents": sum(row["amount_cents"] for row in entries if row["status"] == CLAIMED),
              "pending_cents": sum(row["amount_cents"] for row in entries if row["status"] == PENDING)}
    return {"entries": entries, "days": days, "vouchers": voucher_rows,
            "batches": [{"batch_id": row["batch_id"], "paid_on": row["paid_on"],
                         "status": row["status"], "entries": row["entries"],
                         "issued_at": row["issued_at"]} for row in batches],
            "checks": checks, "all_checks": all(item["ok"] for item in checks), "totals": totals}


def sample():
    batch = json.loads((SAMPLES / "refunds_batch.json").read_text(encoding="utf-8"))
    late = json.loads((SAMPLES / "refunds_vouchers_late.json").read_text(encoding="utf-8"))
    return {"batch": batch, "late": late}
