"""発注書 / 請求書 のデータモデル。

金額は必ず Decimal で持つ。float は丸め誤差で 1 円ずれるので使わない。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, asdict
from decimal import Decimal
from typing import Any

# 消費税率。適格請求書では税率ごとに区分して集計する必要がある。
TAX_RATES = (Decimal("0.10"), Decimal("0.08"), Decimal("0.00"))


def _dec(value: Any, default: str = "0") -> Decimal:
    """JSON から来る数値・文字列・None を Decimal に正規化する。"""
    if value is None or value == "":
        return Decimal(default)
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        # float 経由の誤差を避けるため str を挟む
        return Decimal(str(value))
    if isinstance(value, str):
        cleaned = value.replace(",", "").replace("¥", "").replace("円", "").strip()
        cleaned = cleaned.replace("△", "-").replace("▲", "-")
        if cleaned in ("", "-"):
            return Decimal(default)
        return Decimal(cleaned)
    return Decimal(str(value))


def _date(value: Any) -> dt.date | None:
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日", "%Y.%m.%d"):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


@dataclass
class LineItem:
    """請求明細の 1 行。"""

    name: str
    quantity: Decimal = Decimal("1")
    unit: str = "式"
    unit_price: Decimal = Decimal("0")
    tax_rate: Decimal = Decimal("0.10")
    # 軽減税率(8%)対象である旨は適格請求書の必須記載事項
    reduced_tax: bool = False
    note: str = ""

    def __post_init__(self) -> None:
        self.quantity = _dec(self.quantity, "1")
        self.unit_price = _dec(self.unit_price)
        self.tax_rate = _dec(self.tax_rate, "0.10")
        # 「10」「8」のように % 表記で来たら小数へ寄せる
        if self.tax_rate > 1:
            self.tax_rate = self.tax_rate / Decimal("100")
        self.reduced_tax = bool(self.reduced_tax) or self.tax_rate == Decimal("0.08")

    @property
    def amount(self) -> Decimal:
        """税抜の行合計。円未満は残したまま持ち、集計時にまとめて丸める。"""
        return self.quantity * self.unit_price

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LineItem:
        return cls(
            name=str(data.get("name", "")).strip(),
            quantity=_dec(data.get("quantity"), "1"),
            unit=str(data.get("unit") or "式"),
            unit_price=_dec(data.get("unit_price")),
            tax_rate=_dec(data.get("tax_rate"), "0.10"),
            reduced_tax=bool(data.get("reduced_tax", False)),
            note=str(data.get("note") or ""),
        )


@dataclass
class Party:
    """取引先(請求先)。"""

    name: str = ""
    department: str = ""
    contact: str = ""
    postal_code: str = ""
    address: str = ""
    honorific: str = "御中"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Party:
        data = data or {}
        return cls(
            name=str(data.get("name") or "").strip(),
            department=str(data.get("department") or "").strip(),
            contact=str(data.get("contact") or "").strip(),
            postal_code=str(data.get("postal_code") or "").strip(),
            address=str(data.get("address") or "").strip(),
            honorific=str(data.get("honorific") or "御中").strip(),
        )


@dataclass
class PurchaseOrder:
    """発注書から読み取った内容。ここが人間の確認対象になる。"""

    po_number: str = ""
    order_date: dt.date | None = None
    delivery_date: dt.date | None = None
    subject: str = ""
    buyer: Party = field(default_factory=Party)
    items: list[LineItem] = field(default_factory=list)
    payment_terms: str = ""
    notes: str = ""
    # 発注書に書かれていた合計。読み取り検算にのみ使い、請求額の算出には使わない。
    stated_total: Decimal | None = None
    withholding_tax: bool = False
    # 抽出元の情報（監査証跡）
    source_file: str = ""
    source_sha256: str = ""
    extraction_warnings: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PurchaseOrder:
        stated = data.get("stated_total")
        return cls(
            po_number=str(data.get("po_number") or "").strip(),
            order_date=_date(data.get("order_date")),
            delivery_date=_date(data.get("delivery_date")),
            subject=str(data.get("subject") or "").strip(),
            buyer=Party.from_dict(data.get("buyer") or {}),
            items=[LineItem.from_dict(i) for i in (data.get("items") or [])],
            payment_terms=str(data.get("payment_terms") or "").strip(),
            notes=str(data.get("notes") or "").strip(),
            stated_total=_dec(stated) if stated not in (None, "") else None,
            withholding_tax=bool(data.get("withholding_tax", False)),
            source_file=str(data.get("source_file") or ""),
            source_sha256=str(data.get("source_sha256") or ""),
            extraction_warnings=list(data.get("extraction_warnings") or []),
        )

    def to_dict(self) -> dict[str, Any]:
        """$EDITOR で編集させるための素直な dict（Decimal / date は文字列化）。"""

        def conv(obj: Any) -> Any:
            if isinstance(obj, Decimal):
                return str(obj)
            if isinstance(obj, (dt.date, dt.datetime)):
                return obj.isoformat()
            return obj

        raw = asdict(self)
        return _walk(raw, conv)


def _walk(obj: Any, fn) -> Any:
    if isinstance(obj, dict):
        return {k: _walk(v, fn) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk(v, fn) for v in obj]
    return fn(obj)


@dataclass
class TaxBucket:
    """税率ごとの区分集計。適格請求書の必須記載事項 4/5 に対応する。"""

    rate: Decimal
    net: Decimal  # 税抜合計
    tax: Decimal  # 消費税額（この請求書につき税率ごとに 1 回だけ丸める）

    @property
    def gross(self) -> Decimal:
        return self.net + self.tax

    @property
    def label(self) -> str:
        return f"{int(self.rate * 100)}%"

    @property
    def is_reduced(self) -> bool:
        return self.rate == Decimal("0.08")


@dataclass
class Invoice:
    """発行する請求書。"""

    invoice_number: str
    issue_date: dt.date
    due_date: dt.date | None
    order: PurchaseOrder
    buckets: list[TaxBucket]
    subtotal: Decimal  # 税抜合計
    tax_total: Decimal  # 消費税合計
    withholding: Decimal  # 源泉徴収税額（0 なら非表示）
    total: Decimal  # 請求金額（源泉徴収後）

    @property
    def has_reduced(self) -> bool:
        return any(b.rate == Decimal("0.08") for b in self.buckets)
