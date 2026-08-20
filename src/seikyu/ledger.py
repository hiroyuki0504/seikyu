"""請求書台帳（SQLite）。採番・重複検知・検索・索引簿の出力。

電子帳簿保存法の検索要件（取引年月日・取引金額・取引先）を満たすため、
台帳から索引簿 CSV を出せるようにしてある。
"""

from __future__ import annotations

import csv
import datetime as dt
import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .models import Invoice

SCHEMA = """
CREATE TABLE IF NOT EXISTS invoices (
    invoice_number TEXT PRIMARY KEY,
    year           INTEGER NOT NULL,
    seq            INTEGER NOT NULL,
    issue_date     TEXT NOT NULL,
    due_date       TEXT,
    buyer_name     TEXT NOT NULL,
    subject        TEXT,
    po_number      TEXT,
    subtotal       TEXT NOT NULL,
    tax_total      TEXT NOT NULL,
    withholding    TEXT NOT NULL,
    total          TEXT NOT NULL,
    pdf_path       TEXT NOT NULL,
    source_file    TEXT,
    source_sha256  TEXT,
    created_at     TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'issued',
    paid_at        TEXT,
    note           TEXT
);
CREATE INDEX IF NOT EXISTS idx_invoices_issue_date ON invoices(issue_date);
CREATE INDEX IF NOT EXISTS idx_invoices_buyer      ON invoices(buyer_name);
CREATE INDEX IF NOT EXISTS idx_invoices_sha        ON invoices(source_sha256);
CREATE UNIQUE INDEX IF NOT EXISTS idx_invoices_seq ON invoices(year, seq);

CREATE TABLE IF NOT EXISTS counters (
    year     INTEGER PRIMARY KEY,
    last_seq INTEGER NOT NULL
);

-- 訂正・削除の履歴。電帳法の要請でもあるし、事故調査にも要る。
CREATE TABLE IF NOT EXISTS audit_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    at             TEXT NOT NULL,
    action         TEXT NOT NULL,
    invoice_number TEXT,
    detail         TEXT
);
"""


@dataclass
class LedgerRow:
    invoice_number: str
    issue_date: str
    due_date: str
    buyer_name: str
    subject: str
    po_number: str
    subtotal: Decimal
    tax_total: Decimal
    withholding: Decimal
    total: Decimal
    pdf_path: str
    source_file: str
    source_sha256: str
    status: str
    paid_at: str


class Ledger:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Ledger:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- 採番 -------------------------------------------------------------

    def reserve_number(self, year: int, number_format: str) -> tuple[str, int]:
        """年ごとの連番を 1 つ確保し、(請求書番号, 連番) を返す。

        BEGIN IMMEDIATE で書き込みロックを取り、二重採番を防ぐ。
        """
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute(
                "SELECT last_seq FROM counters WHERE year = ?", (year,)
            ).fetchone()
            seq = (row["last_seq"] if row else 0) + 1
            self.conn.execute(
                "INSERT INTO counters(year, last_seq) VALUES(?, ?) "
                "ON CONFLICT(year) DO UPDATE SET last_seq = excluded.last_seq",
                (year, seq),
            )
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        return number_format.format(year=year, seq=seq, yy=year % 100), seq

    def peek_number(self, year: int, number_format: str) -> str:
        """採番せずに次の番号だけ見る（--dry-run 用）。"""
        row = self.conn.execute(
            "SELECT last_seq FROM counters WHERE year = ?", (year,)
        ).fetchone()
        seq = (row["last_seq"] if row else 0) + 1
        return number_format.format(year=year, seq=seq, yy=year % 100)

    # ---- 記帳 -------------------------------------------------------------

    def find_by_source(self, sha256: str) -> list[sqlite3.Row]:
        if not sha256:
            return []
        return list(
            self.conn.execute(
                "SELECT invoice_number, issue_date, buyer_name, total, pdf_path "
                "FROM invoices WHERE source_sha256 = ? ORDER BY created_at",
                (sha256,),
            )
        )

    def record(self, invoice: Invoice, pdf_path: Path, year: int, seq: int) -> None:
        order = invoice.order
        self.conn.execute(
            """INSERT INTO invoices(
                   invoice_number, year, seq, issue_date, due_date, buyer_name, subject,
                   po_number, subtotal, tax_total, withholding, total, pdf_path,
                   source_file, source_sha256, created_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                invoice.invoice_number,
                year,
                seq,
                invoice.issue_date.isoformat(),
                invoice.due_date.isoformat() if invoice.due_date else None,
                order.buyer.name,
                order.subject,
                order.po_number,
                str(invoice.subtotal),
                str(invoice.tax_total),
                str(invoice.withholding),
                str(invoice.total),
                str(pdf_path),
                order.source_file,
                order.source_sha256,
                dt.datetime.now().isoformat(timespec="seconds"),
            ),
        )
        self.log("issue", invoice.invoice_number, f"{order.buyer.name} / {invoice.total} 円")

    def log(self, action: str, invoice_number: str | None, detail: str) -> None:
        self.conn.execute(
            "INSERT INTO audit_log(at, action, invoice_number, detail) VALUES(?,?,?,?)",
            (dt.datetime.now().isoformat(timespec="seconds"), action, invoice_number, detail),
        )

    def mark_paid(self, invoice_number: str, paid_at: dt.date) -> bool:
        cur = self.conn.execute(
            "UPDATE invoices SET status='paid', paid_at=? WHERE invoice_number=?",
            (paid_at.isoformat(), invoice_number),
        )
        if cur.rowcount:
            self.log("paid", invoice_number, paid_at.isoformat())
        return bool(cur.rowcount)

    # ---- 参照 -------------------------------------------------------------

    def list_invoices(
        self,
        year: int | None = None,
        buyer: str | None = None,
        unpaid_only: bool = False,
    ) -> list[sqlite3.Row]:
        sql = "SELECT * FROM invoices WHERE 1=1"
        params: list[object] = []
        if year is not None:
            sql += " AND year = ?"
            params.append(year)
        if buyer:
            sql += " AND buyer_name LIKE ?"
            params.append(f"%{buyer}%")
        if unpaid_only:
            sql += " AND status != 'paid'"
        sql += " ORDER BY issue_date, invoice_number"
        return list(self.conn.execute(sql, params))

    def export_index_csv(self, out_path: Path, year: int | None = None) -> int:
        """電子帳簿保存法の検索要件を満たす索引簿を出力する。"""
        rows = self.list_invoices(year=year)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(
                [
                    "取引年月日",
                    "取引金額",
                    "取引先",
                    "請求書番号",
                    "件名",
                    "発注書番号",
                    "支払期限",
                    "状態",
                    "入金日",
                    "ファイル",
                ]
            )
            for r in rows:
                writer.writerow(
                    [
                        r["issue_date"],
                        r["total"],
                        r["buyer_name"],
                        r["invoice_number"],
                        r["subject"] or "",
                        r["po_number"] or "",
                        r["due_date"] or "",
                        r["status"],
                        r["paid_at"] or "",
                        r["pdf_path"],
                    ]
                )
        return len(rows)
