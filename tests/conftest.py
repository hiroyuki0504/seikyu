from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from seikyu.config import (
    Bank,
    Company,
    Config,
    InvoiceSettings,
    OutputSettings,
    PaymentSettings,
)
from seikyu.models import LineItem, Party, PurchaseOrder


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(
        company=Company(
            name="株式会社テスト",
            registration_number="T1234567890123",
            postal_code="100-0001",
            address="東京都千代田区1-1-1",
        ),
        bank=Bank(
            bank_name="テスト銀行",
            branch_name="本店",
            account_type="普通",
            account_number="1234567",
            account_holder="カ)テスト",
        ),
        invoice=InvoiceSettings(),
        payment=PaymentSettings(),
        output=OutputSettings(root=tmp_path / "請求書"),
    )


def make_order(items: list[LineItem], **kwargs) -> PurchaseOrder:
    defaults = dict(
        po_number="PO-001",
        order_date=dt.date(2026, 8, 1),
        subject="テスト案件",
        buyer=Party(name="株式会社ABC"),
        items=items,
    )
    defaults.update(kwargs)
    return PurchaseOrder(**defaults)


def item(name: str, qty: str, price: str, rate: str = "0.10") -> LineItem:
    return LineItem(
        name=name,
        quantity=Decimal(qty),
        unit_price=Decimal(price),
        tax_rate=Decimal(rate),
    )
