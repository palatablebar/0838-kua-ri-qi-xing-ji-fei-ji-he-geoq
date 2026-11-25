import pytest


BATCH = {
    "batch_id": "RB20261124",
    "paid_on": "2026-11-24",
    "vouchers": [
        {"voucher_id": "CH1001", "ride_id": "T1", "charged_cents": 150},
    ],
    "refunds": [
        {"refund_id": "RF9101", "amount_cents": 150, "voucher_ref": "CH1001"},
        {"refund_id": "RF9102", "amount_cents": 250, "voucher_ref": "CH1002"},
        {"refund_id": "RF9103", "amount_cents": 100},
        {"refund_id": "RF9104", "amount_cents": 300, "voucher_ref": "CH1003"},
        {"refund_id": "RF9105", "amount_cents": 100, "voucher_ref": "CH1003"},
    ],
}

LATE = {
    "vouchers": [
        {"voucher_id": "CH1002", "ride_id": "T2", "charged_cents": 250, "received_on": "2026-11-25"},
        {"voucher_id": "CH1003", "ride_id": "T3", "charged_cents": 300, "received_on": "2026-11-25"},
    ],
}


def book(client, batch=None):
    response = client.post("/api/refunds/batches", json=batch or BATCH)
    assert response.status_code == 200
    return response.get_json()


def receive(client, late=None):
    response = client.post("/api/refunds/vouchers", json=late or LATE)
    assert response.status_code == 200
    return response.get_json()


def board(client):
    response = client.get("/api/refunds/workbench")
    assert response.status_code == 200
    return response.get_json()


def entry(data, refund_id):
    return next(row for row in data["entries"] if row["refund_id"] == refund_id)


def test_paid_refunds_enter_same_day_settlement(client):
    result = book(client)
    settlement = result["settlement"]
    assert settlement["paid_on"] == "2026-11-24"
    assert settlement["entries"] == 5
    assert settlement["paid_cents"] == 900
    assert settlement["claimed_cents"] + settlement["pending_cents"] == 900


def test_refund_with_same_day_voucher_is_claimed(client):
    result = book(client)
    rf9101 = next(row for row in result["entries"] if row["refund_id"] == "RF9101")
    assert rf9101["status"] == "CLAIMED"
    assert rf9101["reason"] == "VOUCHER_MATCHED"


def test_refund_without_voucher_is_suspense_not_refunded(client):
    book(client)
    data = board(client)
    rf9103 = entry(data, "RF9103")
    assert rf9103["status"] == "PENDING"
    assert rf9103["reason"] == "NO_VOUCHER"
    assert data["totals"]["claimed_cents"] == 150


def test_late_voucher_does_not_block_same_day_booking(client):
    result = book(client)
    rf9102 = next(row for row in result["entries"] if row["refund_id"] == "RF9102")
    assert rf9102["status"] == "PENDING"
    assert rf9102["reason"] == "VOUCHER_LATE"
    assert rf9102["paid_on"] == "2026-11-24"


def test_late_voucher_claims_keep_original_paid_day_and_amount(client):
    book(client)
    receive(client)
    data = board(client)
    rf9102 = entry(data, "RF9102")
    assert rf9102["status"] == "CLAIMED"
    assert rf9102["paid_on"] == "2026-11-24"
    assert rf9102["amount_cents"] == 250
    assert rf9102["claimed_on"] == "2026-11-25"
    day = next(row for row in data["days"] if row["paid_on"] == "2026-11-24")
    assert day["claimed_cents"] == 150 + 250 + 300


def test_cumulative_claim_never_exceeds_charged(client):
    book(client)
    receive(client)
    data = board(client)
    voucher = next(row for row in data["vouchers"] if row["voucher_id"] == "CH1003")
    assert voucher["claimed_cents"] == 300
    assert voucher["claimed_cents"] <= voucher["charged_cents"]
    rf9105 = entry(data, "RF9105")
    assert rf9105["status"] == "PENDING"
    assert rf9105["reason"] == "OVER_REFUND"


def test_cumulative_claim_limit_holds_across_batches(client):
    first = {"batch_id": "B1", "paid_on": "2026-11-24",
             "vouchers": [{"voucher_id": "CH1", "ride_id": "T1", "charged_cents": 500}],
             "refunds": [{"refund_id": "R1", "amount_cents": 400, "voucher_ref": "CH1"}]}
    second = {"batch_id": "B2", "paid_on": "2026-11-25",
              "refunds": [{"refund_id": "R2", "amount_cents": 100, "voucher_ref": "CH1"},
                          {"refund_id": "R3", "amount_cents": 50, "voucher_ref": "CH1"}]}
    book(client, first)
    book(client, second)
    data = board(client)
    voucher = data["vouchers"][0]
    assert voucher["claimed_cents"] == 500
    assert voucher["remaining_cents"] == 0
    assert entry(data, "R2")["status"] == "CLAIMED"
    assert entry(data, "R3")["status"] == "PENDING"
    assert entry(data, "R3")["reason"] == "OVER_REFUND"
    assert data["all_checks"] is True


def test_rerun_same_batch_does_not_double_book(client):
    book(client)
    again = book(client)
    assert again["booked"] == 0
    assert again["duplicates"] == 5
    data = board(client)
    assert data["totals"]["entries"] == 5
    assert data["totals"]["paid_cents"] == 900


def test_rerun_same_vouchers_does_not_double_claim(client):
    book(client)
    receive(client)
    again = receive(client)
    assert again["claimed_total"] == 0
    assert all(item["result"] == "duplicate" for item in again["vouchers"])
    data = board(client)
    assert data["totals"]["claimed_cents"] == 700


def test_conflicting_batch_id_is_rejected(client):
    book(client)
    changed = dict(BATCH, refunds=BATCH["refunds"][:1])
    response = client.post("/api/refunds/batches", json=changed)
    assert response.status_code == 409
    assert response.get_json()["code"] == "BATCH_CONFLICT"


def test_issue_then_workbench_shows_pending_or_claimed(client):
    book(client)
    response = client.post("/api/refunds/batches/RB20261124/issue")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ISSUED"
    receive(client)
    data = board(client)
    assert data["batches"][0]["status"] == "ISSUED"
    assert entry(data, "RF9102")["status"] == "CLAIMED"
    assert entry(data, "RF9103")["status"] == "PENDING"
    assert entry(data, "RF9105")["status"] == "PENDING"


def test_issue_unknown_batch_is_404(client):
    response = client.post("/api/refunds/batches/NOPE/issue")
    assert response.status_code == 404
    assert response.get_json()["code"] == "BATCH_UNKNOWN"


def test_double_issue_is_rejected(client):
    book(client)
    assert client.post("/api/refunds/batches/RB20261124/issue").status_code == 200
    response = client.post("/api/refunds/batches/RB20261124/issue")
    assert response.status_code == 409
    assert response.get_json()["code"] == "BATCH_ALREADY_ISSUED"


def test_workbench_checks_pass_after_full_scenario(client):
    book(client)
    receive(client)
    data = board(client)
    assert data["all_checks"] is True
    assert all(item["ok"] for item in data["checks"])


def test_day_totals_balance(client):
    book(client)
    receive(client)
    for day in board(client)["days"]:
        assert day["paid_cents"] == day["claimed_cents"] + day["pending_cents"]


@pytest.mark.parametrize("patch, code", [
    ({"batch_id": ""}, "REFUND_BAD_FIELD"),
    ({"paid_on": "24/11/2026"}, "REFUND_BAD_DATE"),
    ({"refunds": []}, "REFUND_BAD_FIELD"),
])
def test_batch_validation(client, patch, code):
    response = client.post("/api/refunds/batches", json=dict(BATCH, **patch))
    assert response.status_code == 400
    assert response.get_json()["code"] == code


@pytest.mark.parametrize("amount", [0, -50, "2.50", 2.5, None])
def test_amount_must_be_positive_integer_cents(client, amount):
    batch = dict(BATCH, refunds=[{"refund_id": "RF1", "amount_cents": amount}])
    response = client.post("/api/refunds/batches", json=batch)
    assert response.status_code == 400
    assert response.get_json()["code"] == "REFUND_BAD_AMOUNT"


def test_duplicate_refund_id_inside_batch_is_rejected(client):
    batch = dict(BATCH, refunds=[{"refund_id": "RF1", "amount_cents": 100},
                                 {"refund_id": "RF1", "amount_cents": 200}])
    response = client.post("/api/refunds/batches", json=batch)
    assert response.status_code == 400


def test_voucher_validation(client):
    response = client.post("/api/refunds/vouchers", json={"vouchers": []})
    assert response.status_code == 400
    bad = {"vouchers": [{"voucher_id": "CH1", "ride_id": "T1", "charged_cents": -1}]}
    response = client.post("/api/refunds/vouchers", json=bad)
    assert response.status_code == 400
    assert response.get_json()["code"] == "REFUND_BAD_AMOUNT"


def test_sample_endpoint_matches_files(client):
    sample = client.get("/api/refunds/sample").get_json()
    assert sample["batch"]["batch_id"] == "RB20261124"
    assert len(sample["late"]["vouchers"]) == 2
