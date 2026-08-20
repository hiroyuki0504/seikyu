"""発注書がない・読み取らせたくないときの手入力。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation

from rich.console import Console

from ..models import LineItem, Party, PurchaseOrder, _date, _dec


def _ask(console: Console, label: str, default: str = "") -> str:
    suffix = f" [dim]({default})[/dim]" if default else ""
    value = console.input(f"{label}{suffix}: ").strip()
    return value or default


def _ask_decimal(console: Console, label: str, default: str = "") -> Decimal:
    while True:
        raw = _ask(console, label, default)
        try:
            return _dec(raw)
        except InvalidOperation:
            console.print("[yellow]数字で入力してください。[/yellow]")


def _ask_date(console: Console, label: str, default: dt.date | None) -> dt.date | None:
    while True:
        raw = _ask(console, label, default.isoformat() if default else "")
        if not raw:
            return None
        parsed = _date(raw)
        if parsed:
            return parsed
        console.print("[yellow]YYYY-MM-DD の形式で入力してください。[/yellow]")


def prompt_order(console: Console) -> PurchaseOrder:
    console.print("[bold cyan]発注内容を入力します（Ctrl-C で中止）[/bold cyan]\n")

    buyer = Party(
        name=_ask(console, "請求先 会社名"),
        honorific=_ask(console, "敬称", "御中"),
        department=_ask(console, "部署"),
        contact=_ask(console, "担当者名"),
        postal_code=_ask(console, "郵便番号"),
        address=_ask(console, "住所"),
    )
    while not buyer.name:
        console.print("[yellow]請求先の会社名は必須です。[/yellow]")
        buyer.name = _ask(console, "請求先 会社名")

    subject = _ask(console, "件名")
    po_number = _ask(console, "発注書番号")
    order_date = _ask_date(console, "発注日", dt.date.today())

    console.print("\n[bold]明細[/bold]（品名を空のまま Enter で入力終了）")
    items: list[LineItem] = []
    while True:
        console.print(f"\n[dim]-- {len(items) + 1} 行目 --[/dim]")
        name = _ask(console, "品名")
        if not name:
            break
        qty = _ask_decimal(console, "数量", "1")
        unit = _ask(console, "単位", "式")
        price = _ask_decimal(console, "単価（税抜）")
        rate = _ask(console, "税率 %", "10")
        items.append(
            LineItem(
                name=name,
                quantity=qty,
                unit=unit,
                unit_price=price,
                tax_rate=_dec(rate, "10"),
                note=_ask(console, "備考"),
            )
        )

    if not items:
        raise RuntimeError("明細が 1 行もありません。")

    notes = _ask(console, "\n請求書の備考欄")

    return PurchaseOrder(
        po_number=po_number,
        order_date=order_date,
        subject=subject,
        buyer=buyer,
        items=items,
        notes=notes,
        source_file="(手入力)",
    )
