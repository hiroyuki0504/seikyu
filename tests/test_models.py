from __future__ import annotations

import datetime as dt
from decimal import Decimal

from seikyu.models import LineItem, PurchaseOrder, _date, _dec


def test_dec_strips_japanese_money_notation():
    assert _dec("1,234") == Decimal("1234")
    assert _dec("¥1,000") == Decimal("1000")
    assert _dec("1000円") == Decimal("1000")
    assert _dec("△500") == Decimal("-500")
    assert _dec("▲500") == Decimal("-500")
    assert _dec("") == Decimal("0")
    assert _dec(None) == Decimal("0")


def test_dec_avoids_float_error():
    # 0.1 + 0.2 問題を持ち込まないこと
    assert _dec(0.07) == Decimal("0.07")


def test_date_parses_common_japanese_formats():
    assert _date("2026-08-18") == dt.date(2026, 8, 18)
    assert _date("2026/08/18") == dt.date(2026, 8, 18)
    assert _date("2026年8月18日") == dt.date(2026, 8, 18)
    assert _date("") is None
    assert _date("不明") is None


def test_percent_tax_rate_is_normalized():
    assert LineItem(name="x", tax_rate=Decimal("10")).tax_rate == Decimal("0.10")
    assert LineItem(name="x", tax_rate=Decimal("8")).tax_rate == Decimal("0.08")
    assert LineItem(name="x", tax_rate=Decimal("0.10")).tax_rate == Decimal("0.10")


def test_eight_percent_marks_reduced_tax():
    assert LineItem(name="x", tax_rate=Decimal("8")).reduced_tax is True
    assert LineItem(name="x", tax_rate=Decimal("10")).reduced_tax is False


def test_line_amount():
    it = LineItem(name="x", quantity=Decimal("3"), unit_price=Decimal("1500"))
    assert it.amount == Decimal("4500")


def test_from_dict_round_trip():
    order = PurchaseOrder.from_dict(
        {
            "po_number": "PO-9",
            "order_date": "2026/08/01",
            "buyer": {"name": "株式会社ABC", "honorific": "御中"},
            "items": [
                {"name": "制作費", "quantity": "1", "unit_price": "300,000", "tax_rate": "10"},
                {"name": "菓子", "quantity": "2", "unit_price": "500", "tax_rate": "8"},
            ],
            "stated_total": "331,080",
        }
    )
    assert order.order_date == dt.date(2026, 8, 1)
    assert order.items[0].unit_price == Decimal("300000")
    assert order.items[1].tax_rate == Decimal("0.08")
    assert order.stated_total == Decimal("331080")

    payload = order.to_dict()
    assert payload["items"][0]["unit_price"] == "300000"
    restored = PurchaseOrder.from_dict(payload)
    assert restored.items[0].unit_price == order.items[0].unit_price
    assert restored.buyer.name == "株式会社ABC"


def test_missing_quantity_defaults_to_one():
    order = PurchaseOrder.from_dict({"items": [{"name": "一式", "unit_price": "1000"}]})
    assert order.items[0].quantity == Decimal("1")
    assert order.items[0].unit == "式"
