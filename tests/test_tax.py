from __future__ import annotations

import datetime as dt
from decimal import Decimal

from conftest import item, make_order

from seikyu.tax import (
    adjust_business_day,
    build_invoice,
    closing_date,
    due_date,
    payment_terms_label,
    verify_against_stated_total,
    withholding_amount,
)


def test_single_rate(cfg):
    order = make_order([item("Web制作", "1", "300000")])
    inv = build_invoice(order, "INV-2026-0001", dt.date(2026, 8, 18), cfg)
    assert inv.subtotal == Decimal("300000")
    assert inv.tax_total == Decimal("30000")
    assert inv.total == Decimal("330000")
    assert len(inv.buckets) == 1
    assert inv.buckets[0].label == "10%"


def test_mixed_rates_are_separated(cfg):
    order = make_order(
        [
            item("制作費", "1", "300000", "0.10"),
            item("茶菓子", "10", "500", "0.08"),
        ]
    )
    inv = build_invoice(order, "INV-2026-0002", dt.date(2026, 8, 18), cfg)

    by_rate = {b.label: b for b in inv.buckets}
    assert by_rate["10%"].net == Decimal("300000")
    assert by_rate["10%"].tax == Decimal("30000")
    assert by_rate["8%"].net == Decimal("5000")
    assert by_rate["8%"].tax == Decimal("400")

    assert inv.subtotal == Decimal("305000")
    assert inv.tax_total == Decimal("30400")
    assert inv.total == Decimal("335400")
    assert inv.has_reduced is True


def test_tax_rounded_once_per_rate_not_per_line(cfg):
    """105円×3行。行ごとに丸めると30円、正しくは合計315円に対して31円。"""
    order = make_order([item(f"部品{i}", "1", "105") for i in range(3)])
    inv = build_invoice(order, "INV-2026-0003", dt.date(2026, 8, 18), cfg)
    assert inv.subtotal == Decimal("315")
    assert inv.tax_total == Decimal("31")  # floor(31.5)。行ごと丸めなら30になる


def test_rounding_modes(cfg):
    order = make_order([item("端数", "1", "105")])

    cfg.invoice.rounding = "floor"
    assert build_invoice(order, "X", dt.date(2026, 8, 18), cfg).tax_total == Decimal("10")

    cfg.invoice.rounding = "ceil"
    assert build_invoice(order, "X", dt.date(2026, 8, 18), cfg).tax_total == Decimal("11")

    cfg.invoice.rounding = "round"
    assert build_invoice(order, "X", dt.date(2026, 8, 18), cfg).tax_total == Decimal("11")


def test_discount_line_is_negative(cfg):
    order = make_order(
        [
            item("制作費", "1", "300000"),
            item("値引き", "1", "-50000"),
        ]
    )
    inv = build_invoice(order, "INV-2026-0004", dt.date(2026, 8, 18), cfg)
    assert inv.subtotal == Decimal("250000")
    assert inv.tax_total == Decimal("25000")
    assert inv.total == Decimal("275000")


def test_withholding_thresholds():
    assert withholding_amount(Decimal("300000")) == Decimal("30630")
    assert withholding_amount(Decimal("1000000")) == Decimal("102100")
    assert withholding_amount(Decimal("2000000")) == Decimal("306300")
    assert withholding_amount(Decimal("0")) == Decimal("0")


def test_withholding_applied_from_order_flag(cfg):
    order = make_order([item("原稿料", "1", "100000")], withholding_tax=True)
    inv = build_invoice(order, "INV-2026-0005", dt.date(2026, 8, 18), cfg)
    assert inv.withholding == Decimal("10210")  # 税抜10万 × 10.21%
    assert inv.total == Decimal("99790")  # 100000 + 10000 - 10210


def test_withholding_base_gross(cfg):
    cfg.invoice.withholding = True
    cfg.invoice.withholding_base = "gross"
    order = make_order([item("原稿料", "1", "100000")])
    inv = build_invoice(order, "INV-2026-0006", dt.date(2026, 8, 18), cfg)
    assert inv.withholding == Decimal("11231")  # 110000 × 10.21%


def test_closing_and_due_month_end(cfg):
    assert closing_date(dt.date(2026, 8, 18), 31) == dt.date(2026, 8, 31)
    assert due_date(dt.date(2026, 8, 18), cfg) == dt.date(2026, 9, 30)


def test_closing_rolls_to_next_month_when_past(cfg):
    cfg.payment.closing_day = 20
    cfg.payment.payment_day = 20
    assert closing_date(dt.date(2026, 8, 25), 20) == dt.date(2026, 9, 20)
    assert due_date(dt.date(2026, 8, 25), cfg) == dt.date(2026, 10, 20)


def test_closing_clamps_short_month(cfg):
    # 2月に31日はない
    assert closing_date(dt.date(2026, 2, 10), 31) == dt.date(2026, 2, 28)


def test_business_day_adjust_moves_off_weekend():
    saturday = dt.date(2026, 8, 22)
    assert saturday.weekday() == 5
    assert adjust_business_day(saturday, "none") == saturday
    assert adjust_business_day(saturday, "before") == dt.date(2026, 8, 21)
    assert adjust_business_day(saturday, "after") == dt.date(2026, 8, 24)


def test_payment_terms_label(cfg):
    assert payment_terms_label(cfg) == "月末締め 翌月末払い"
    cfg.payment.closing_day = 20
    cfg.payment.payment_day = 10
    cfg.payment.month_offset = 2
    assert payment_terms_label(cfg) == "20日締め 翌々月10日払い"
    cfg.payment.terms_label = "都度払い"
    assert payment_terms_label(cfg) == "都度払い"


def test_stated_total_mismatch_is_reported(cfg):
    order = make_order([item("制作費", "1", "300000")], stated_total=Decimal("330000"))
    inv = build_invoice(order, "INV-2026-0007", dt.date(2026, 8, 18), cfg)
    assert verify_against_stated_total(inv) is None

    order2 = make_order([item("制作費", "1", "300000")], stated_total=Decimal("350000"))
    inv2 = build_invoice(order2, "INV-2026-0008", dt.date(2026, 8, 18), cfg)
    assert "ずれています" in verify_against_stated_total(inv2)
