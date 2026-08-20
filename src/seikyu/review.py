"""発行前の確認・編集。

お金の書類なので、既定では必ず人間が目視してから PDF を作る。
--yes を付けたときだけ確認を飛ばす。
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .config import Config
from .models import Invoice, PurchaseOrder
from .render import jp_date, quantity, yen
from .tax import build_invoice, payment_terms_label, verify_against_stated_total


class ReviewAborted(RuntimeError):
    pass


def _kv(label: str, value: str) -> Text:
    line = Text()
    line.append(f"{label:<10}", style="dim")
    line.append(value or "—")
    return line


def render_summary(console: Console, invoice: Invoice, cfg: Config) -> None:
    order = invoice.order

    head = Text()
    for label, value in (
        ("請求先", f"{order.buyer.name}　{order.buyer.honorific}"),
        ("担当", "　".join(x for x in (order.buyer.department, order.buyer.contact) if x)),
        ("件名", order.subject),
        ("発注書番号", order.po_number),
        ("発注日", jp_date(order.order_date)),
        ("請求書番号", invoice.invoice_number),
        ("発行日", jp_date(invoice.issue_date)),
        ("支払期限", f"{jp_date(invoice.due_date)}（{payment_terms_label(cfg)}）"),
    ):
        head.append_text(_kv(label, value))
        head.append("\n")
    console.print(Panel(head, title="請求書の内容", border_style="cyan", padding=(0, 2)))

    table = Table(show_edge=False, header_style="bold", padding=(0, 1))
    table.add_column("#", justify="right", style="dim", width=3)
    table.add_column("品目")
    table.add_column("数量", justify="right")
    table.add_column("単位")
    table.add_column("単価", justify="right")
    table.add_column("金額", justify="right")
    table.add_column("税率", justify="right")

    from .tax import line_amount

    for i, item in enumerate(order.items, 1):
        table.add_row(
            str(i),
            item.name + ("  ※" if item.reduced_tax else ""),
            quantity(item.quantity),
            item.unit,
            yen(item.unit_price),
            yen(line_amount(item, cfg.invoice.rounding)),
            f"{int(item.tax_rate * 100)}%",
        )
    console.print(table)

    sums = Table(show_header=False, show_edge=False, box=None, padding=(0, 1))
    sums.add_column(justify="right", style="dim", width=16)
    sums.add_column(justify="right", width=14)
    for bucket in invoice.buckets:
        sums.add_row(f"{bucket.label}対象 税抜", yen(bucket.net))
        sums.add_row(f"{bucket.label} 消費税", yen(bucket.tax))
    sums.add_row("小計（税抜）", yen(invoice.subtotal))
    sums.add_row("消費税", yen(invoice.tax_total))
    if invoice.withholding:
        sums.add_row("源泉徴収", f"△{yen(invoice.withholding)}")
    sums.add_row(Text("合計", style="bold"), Text(f"¥ {yen(invoice.total)}", style="bold"))
    console.print(sums)

    warnings = list(order.extraction_warnings)
    mismatch = verify_against_stated_total(invoice)
    if mismatch:
        warnings.append(mismatch)
    if warnings:
        body = Text("\n".join(f"・{w}" for w in warnings))
        console.print(Panel(body, title="要確認", border_style="yellow", padding=(0, 2)))


def edit_in_editor(order: PurchaseOrder) -> PurchaseOrder:
    """$EDITOR で JSON を直接いじらせる。項目が多いのでフォーム入力より速い。"""
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
    payload = order.to_dict()
    # 監査情報は編集させない
    for key in ("source_file", "source_sha256", "extraction_warnings"):
        payload.pop(key, None)

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "purchase_order.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        subprocess.run([*editor.split(), str(path)], check=False)
        try:
            edited = json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
        except json.JSONDecodeError as exc:
            raise ReviewAborted(f"編集後の JSON を読めませんでした: {exc}") from exc

    edited["source_file"] = order.source_file
    edited["source_sha256"] = order.source_sha256
    edited["extraction_warnings"] = order.extraction_warnings
    try:
        return PurchaseOrder.from_dict(edited)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ReviewAborted(f"編集内容を解釈できませんでした: {exc}") from exc


def confirm(
    console: Console,
    order: PurchaseOrder,
    invoice_number: str,
    issue_date,
    cfg: Config,
) -> tuple[PurchaseOrder, Invoice]:
    """内容を表示し、承認されるまで編集ループを回す。"""
    while True:
        invoice = build_invoice(order, invoice_number, issue_date, cfg)
        render_summary(console, invoice, cfg)
        console.print(
            "\n[bold]Enter[/bold]=この内容で発行　"
            "[bold]e[/bold]=編集　"
            "[bold]q[/bold]=中止",
            highlight=False,
        )
        try:
            answer = console.input("> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            raise ReviewAborted("中止しました。") from None

        if answer in ("", "y", "yes"):
            return order, invoice
        if answer in ("q", "n", "no"):
            raise ReviewAborted("中止しました。")
        if answer == "e":
            order = edit_in_editor(order)
            console.print()
            continue
        console.print("[yellow]Enter / e / q のいずれかを入力してください。[/yellow]")
