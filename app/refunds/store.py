import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.evidence.manifest import stable_digest

PENDING = "PENDING"
CLAIMED = "CLAIMED"


class RefundStore:
    """退款日结台账：银行出款、原扣款凭据与认退状态。

    与批次复核共用同一个 SQLite 文件，各表独立。所有写操作都在
    BEGIN IMMEDIATE 事务内完成，保证日结入账与凭据认领的原子性。
    """

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=8)
        connection.row_factory = sqlite3.Row
        return connection

    def init(self):
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS refund_batches(
              batch_id TEXT PRIMARY KEY,
              paid_on TEXT NOT NULL,
              digest TEXT NOT NULL,
              status TEXT NOT NULL,
              entries INTEGER NOT NULL,
              created_at TEXT NOT NULL,
              issued_at TEXT
            );
            CREATE TABLE IF NOT EXISTS refund_vouchers(
              voucher_id TEXT PRIMARY KEY,
              ride_id TEXT NOT NULL,
              charged_cents INTEGER NOT NULL,
              received_on TEXT NOT NULL,
              batch_id TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS refund_entries(
              refund_id TEXT PRIMARY KEY,
              batch_id TEXT NOT NULL,
              paid_on TEXT NOT NULL,
              amount_cents INTEGER NOT NULL,
              voucher_ref TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL,
              reason TEXT NOT NULL,
              claimed_on TEXT,
              created_at TEXT NOT NULL
            );
            ''')

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def digest_of(payload):
        return stable_digest(payload)

    def get_batch(self, batch_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM refund_batches WHERE batch_id=?", (batch_id,)).fetchone()
        return dict(row) if row else None

    def _claimed_cents(self, db, voucher_id):
        row = db.execute(
            "SELECT COALESCE(SUM(amount_cents),0) AS total FROM refund_entries "
            "WHERE voucher_ref=? AND status=?", (voucher_id, CLAIMED)).fetchone()
        return int(row["total"])

    def _insert_voucher(self, db, voucher, batch_id):
        db.execute(
            "INSERT INTO refund_vouchers(voucher_id,ride_id,charged_cents,received_on,batch_id) "
            "VALUES(?,?,?,?,?)",
            (voucher["voucher_id"], voucher["ride_id"], voucher["charged_cents"],
             voucher["received_on"], batch_id))

    def _claim_pending(self, db, voucher):
        """凭据补齐后认领暂挂款：按出款日先后认退，累计不超过实扣。

        认领只翻转状态，原出款日与金额照旧。
        """
        claimed, blocked = [], []
        rows = db.execute(
            "SELECT rowid,* FROM refund_entries WHERE voucher_ref=? AND status=? "
            "ORDER BY paid_on, rowid", (voucher["voucher_id"], PENDING)).fetchall()
        for row in rows:
            claimed_so_far = self._claimed_cents(db, voucher["voucher_id"])
            if claimed_so_far + row["amount_cents"] <= voucher["charged_cents"]:
                db.execute("UPDATE refund_entries SET status=?, reason=?, claimed_on=? WHERE refund_id=?",
                           (CLAIMED, "VOUCHER_MATCHED", voucher["received_on"], row["refund_id"]))
                claimed.append(row["refund_id"])
            else:
                db.execute("UPDATE refund_entries SET reason=? WHERE refund_id=?",
                           ("OVER_REFUND", row["refund_id"]))
                blocked.append(row["refund_id"])
        return claimed, blocked

    def book(self, batch_id, paid_on, refunds, vouchers, digest):
        """日结入账：银行当天已付退款全部进入当日结算。

        凭据先到先登记；退款按凭据情况记为已退或暂挂。同一批重跑时
        已入账的退款与凭据按主键跳过，不重复记账。
        """
        now = self._now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            voucher_results = []
            for voucher in vouchers:
                exists = db.execute("SELECT 1 FROM refund_vouchers WHERE voucher_id=?",
                                    (voucher["voucher_id"],)).fetchone()
                if exists:
                    voucher_results.append({"voucher_id": voucher["voucher_id"], "result": "duplicate"})
                    continue
                self._insert_voucher(db, voucher, batch_id)
                voucher_results.append({"voucher_id": voucher["voucher_id"], "result": "registered"})
            entry_results = []
            for refund in refunds:
                exists = db.execute("SELECT 1 FROM refund_entries WHERE refund_id=?",
                                    (refund["refund_id"],)).fetchone()
                if exists:
                    entry_results.append({"refund_id": refund["refund_id"], "result": "duplicate"})
                    continue
                voucher_ref = refund["voucher_ref"]
                status, reason, claimed_on = PENDING, "NO_VOUCHER", None
                if voucher_ref:
                    voucher = db.execute("SELECT * FROM refund_vouchers WHERE voucher_id=?",
                                         (voucher_ref,)).fetchone()
                    if voucher is None:
                        reason = "VOUCHER_LATE"
                    elif self._claimed_cents(db, voucher_ref) + refund["amount_cents"] <= voucher["charged_cents"]:
                        status, reason, claimed_on = CLAIMED, "VOUCHER_MATCHED", paid_on
                    else:
                        reason = "OVER_REFUND"
                db.execute(
                    "INSERT INTO refund_entries(refund_id,batch_id,paid_on,amount_cents,voucher_ref,"
                    "status,reason,claimed_on,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (refund["refund_id"], batch_id, paid_on, refund["amount_cents"], voucher_ref,
                     status, reason, claimed_on, now))
                entry_results.append({"refund_id": refund["refund_id"], "result": "booked",
                                      "status": status, "reason": reason, "paid_on": paid_on,
                                      "amount_cents": refund["amount_cents"], "voucher_ref": voucher_ref})
            db.execute(
                "INSERT OR IGNORE INTO refund_batches(batch_id,paid_on,digest,status,entries,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (batch_id, paid_on, digest, "BOOKED", len(refunds), now))
        return {"vouchers": voucher_results, "entries": entry_results}

    def receive(self, vouchers):
        """凭据补到：登记晚到的原扣款凭据，并把匹配的暂挂款转为已退。"""
        results = []
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for voucher in vouchers:
                exists = db.execute("SELECT 1 FROM refund_vouchers WHERE voucher_id=?",
                                    (voucher["voucher_id"],)).fetchone()
                if exists:
                    results.append({"voucher_id": voucher["voucher_id"], "result": "duplicate",
                                    "claimed": [], "blocked": []})
                    continue
                self._insert_voucher(db, voucher, "LATE")
                claimed, blocked = self._claim_pending(db, voucher)
                results.append({"voucher_id": voucher["voucher_id"], "result": "registered",
                                "claimed": claimed, "blocked": blocked})
        return results

    def issue(self, batch_id):
        """签发日结批次。签发后暂挂款仍可随凭据补到继续认领。"""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM refund_batches WHERE batch_id=?", (batch_id,)).fetchone()
            if row is None:
                return None, "missing"
            if row["status"] == "ISSUED":
                return None, "issued"
            db.execute("UPDATE refund_batches SET status=?, issued_at=? WHERE batch_id=?",
                       ("ISSUED", self._now(), batch_id))
        return self.get_batch(batch_id), None

    def entries(self):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM refund_entries ORDER BY paid_on, rowid").fetchall()
        return [dict(row) for row in rows]

    def vouchers(self):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM refund_vouchers ORDER BY received_on, rowid").fetchall()
        return [dict(row) for row in rows]

    def batches(self):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM refund_batches ORDER BY paid_on, batch_id").fetchall()
        return [dict(row) for row in rows]
