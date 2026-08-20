"""seikyu — 発注書を入れると適格請求書を作って保存するローカルツール。"""

from __future__ import annotations

import datetime as dt
import shutil
import time
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from . import config as config_mod
from .config import Config, ConfigError
from .extract import ExtractionError, extract
from .ingest.manual import prompt_order
from .ledger import Ledger
from .models import PurchaseOrder
from .naming import output_path, unique_path
from .render import RenderError, find_browser, render_pdf
from .review import ReviewAborted, confirm, render_summary
from .tax import build_invoice

app = typer.Typer(
    add_completion=False,
    help="発注書から適格請求書（インボイス）を作って保存します。",
    no_args_is_help=True,
)
console = Console()

SUPPORTED_SUFFIXES = {
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".heic", ".heif", ".tif", ".tiff", ".bmp",
    ".xlsx", ".xlsm", ".csv", ".tsv",
}

ConfigOpt = typer.Option(None, "--config", "-c", help="設定ファイルのパス", show_default=False)


def _load(config_path: Optional[Path]) -> Config:
    try:
        cfg = config_mod.load(config_path)
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    problems = cfg.validate()
    if problems:
        console.print(f"[red]設定に不足があります（{cfg.path}）:[/red]")
        for p in problems:
            # [company] などの角括弧を rich のマークアップとして食わせない
            console.print(f"  ・{p}", markup=False)
        raise typer.Exit(1)
    return cfg


def _ledger_path(cfg: Config) -> Path:
    return cfg.output.root / "_台帳" / "seikyu.db"


def _parse_date(value: Optional[str]) -> dt.date:
    if not value:
        return dt.date.today()
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        console.print(f"[red]日付は YYYY-MM-DD の形式で指定してください: {value}[/red]")
        raise typer.Exit(1) from exc


def _issue_one(
    order: PurchaseOrder,
    cfg: Config,
    ledger: Ledger,
    issue_date: dt.date,
    auto_yes: bool,
    dry_run: bool,
) -> Optional[Path]:
    """1 件を確認 → 採番 → PDF → 記帳。中止なら None。"""
    year = issue_date.year

    if order.source_sha256:
        dups = ledger.find_by_source(order.source_sha256)
        if dups:
            console.print(
                f"[yellow]この発注書は既に請求済みです: "
                f"{', '.join(d['invoice_number'] for d in dups)}[/yellow]"
            )
            if auto_yes:
                console.print("[yellow]--yes 指定のため二重発行を避けてスキップします。[/yellow]")
                return None
            if not typer.confirm("それでも発行しますか？", default=False):
                return None

    preview_number = ledger.peek_number(year, cfg.invoice.number_format)

    if auto_yes:
        invoice = build_invoice(order, preview_number, issue_date, cfg)
        render_summary(console, invoice, cfg)
    else:
        try:
            order, invoice = confirm(console, order, preview_number, issue_date, cfg)
        except ReviewAborted as exc:
            console.print(f"[yellow]{exc}[/yellow]")
            return None

    if dry_run:
        planned = output_path(invoice, cfg)
        console.print(f"\n[cyan]--dry-run: 発行しません。[/cyan] 保存予定 → {planned}")
        return None

    # 確認が通ってから採番する。中止した番号を欠番にしないため。
    number, seq = ledger.reserve_number(year, cfg.invoice.number_format)
    invoice = build_invoice(order, number, issue_date, cfg)

    pdf_path = unique_path(output_path(invoice, cfg))
    try:
        render_pdf(invoice, cfg, pdf_path)
    except RenderError as exc:
        ledger.log("render_failed", number, str(exc))
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    if cfg.output.keep_source and order.source_file and order.source_file != "(手入力)":
        src = Path(order.source_file)
        if src.exists():
            archive = pdf_path.parent / f"{pdf_path.stem}_発注書{src.suffix}"
            shutil.copy2(src, archive)

    ledger.record(invoice, pdf_path, year, seq)
    console.print(f"\n[green]発行しました[/green] {number} → {pdf_path}")
    return pdf_path


@app.command()
def init(config: Optional[Path] = ConfigOpt) -> None:
    """設定ファイルのひな形を作る。"""
    target = Path(config) if config else config_mod.DEFAULT_CONFIG
    if target.exists():
        console.print(f"[yellow]既にあります: {target}[/yellow]")
        raise typer.Exit(0)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(config_mod.EXAMPLE_CONFIG, target)
    console.print(f"[green]作成しました:[/green] {target}")
    console.print("自社名・登録番号・住所・振込先を記入してから `seikyu doctor` を実行してください。")


@app.command()
def issue(
    files: list[Path] = typer.Argument(..., help="発注書ファイル（PDF / Excel / CSV / 画像）"),
    date: Optional[str] = typer.Option(None, "--date", "-d", help="発行日 (YYYY-MM-DD)。既定は今日"),
    yes: bool = typer.Option(False, "--yes", "-y", help="確認を省いて発行する"),
    dry_run: bool = typer.Option(False, "--dry-run", help="発行せず内容だけ確認する"),
    config: Optional[Path] = ConfigOpt,
) -> None:
    """発注書から請求書を発行する。"""
    cfg = _load(config)
    issue_date = _parse_date(date)

    with Ledger(_ledger_path(cfg)) as ledger:
        for path in files:
            path = path.expanduser()
            if not path.exists():
                console.print(f"[red]ファイルがありません: {path}[/red]")
                continue
            console.print(f"\n[bold]読み取り中[/bold] {path.name} …")
            try:
                order = extract(path, cfg)
            except ExtractionError as exc:
                console.print(f"[red]{exc}[/red]")
                continue
            _issue_one(order, cfg, ledger, issue_date, yes, dry_run)


@app.command()
def new(
    date: Optional[str] = typer.Option(None, "--date", "-d", help="発行日 (YYYY-MM-DD)"),
    config: Optional[Path] = ConfigOpt,
) -> None:
    """発注書なしで、手入力から請求書を作る。"""
    cfg = _load(config)
    issue_date = _parse_date(date)
    try:
        order = prompt_order(console)
    except (KeyboardInterrupt, EOFError):
        console.print("\n[yellow]中止しました。[/yellow]")
        raise typer.Exit(1) from None
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    with Ledger(_ledger_path(cfg)) as ledger:
        _issue_one(order, cfg, ledger, issue_date, False, False)


@app.command()
def watch(
    interval: float = typer.Option(5.0, "--interval", help="フォルダを見に行く間隔（秒）"),
    yes: bool = typer.Option(False, "--yes", "-y", help="確認なしで自動発行する"),
    config: Optional[Path] = ConfigOpt,
) -> None:
    """受信フォルダを見張り、発注書が置かれたら請求書を作る。"""
    cfg = _load(config)
    w = cfg.watch
    for folder in (w.inbox, w.processed, w.failed):
        folder.mkdir(parents=True, exist_ok=True)

    console.print(f"[bold cyan]監視中[/bold cyan] {w.inbox}")
    console.print("発注書をこのフォルダに入れてください。Ctrl-C で終了します。\n")

    sizes: dict[Path, int] = {}
    with Ledger(_ledger_path(cfg)) as ledger:
        try:
            while True:
                for path in sorted(w.inbox.iterdir()):
                    if path.is_dir() or path.name.startswith("."):
                        continue
                    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
                        continue
                    size = path.stat().st_size
                    # コピー途中のファイルを掴まないよう、サイズが 1 周期変わらないのを待つ
                    if sizes.get(path) != size:
                        sizes[path] = size
                        continue
                    sizes.pop(path, None)

                    console.print(f"\n[bold]検出[/bold] {path.name}")
                    try:
                        order = extract(path, cfg)
                        result = _issue_one(order, cfg, ledger, dt.date.today(), yes, False)
                    except ExtractionError as exc:
                        console.print(f"[red]{exc}[/red]")
                        result = None
                    dest_dir = w.processed if result else w.failed
                    dest = unique_path(dest_dir / path.name)
                    shutil.move(str(path), dest)
                    console.print(f"[dim]移動: {dest}[/dim]")
                time.sleep(interval)
        except KeyboardInterrupt:
            console.print("\n[yellow]監視を終了しました。[/yellow]")


@app.command("list")
def list_invoices(
    year: Optional[int] = typer.Option(None, "--year", "-y", help="対象年"),
    buyer: Optional[str] = typer.Option(None, "--buyer", "-b", help="取引先名の部分一致"),
    unpaid: bool = typer.Option(False, "--unpaid", help="未入金のみ"),
    config: Optional[Path] = ConfigOpt,
) -> None:
    """発行済み請求書の一覧を出す。"""
    cfg = _load(config)
    with Ledger(_ledger_path(cfg)) as ledger:
        rows = ledger.list_invoices(year=year, buyer=buyer, unpaid_only=unpaid)

    if not rows:
        console.print("[dim]該当する請求書はありません。[/dim]")
        return

    table = Table(header_style="bold")
    # 番号・日付・金額は折り返さない。潰れてよいのは取引先と件名だけ。
    table.add_column("請求書番号", no_wrap=True)
    table.add_column("発行日", no_wrap=True)
    table.add_column("取引先", overflow="ellipsis", max_width=22)
    table.add_column("件名", overflow="ellipsis", max_width=20)
    table.add_column("金額", justify="right", no_wrap=True)
    table.add_column("支払期限", no_wrap=True)
    table.add_column("状態", no_wrap=True)

    total = 0
    today = dt.date.today().isoformat()
    for r in rows:
        overdue = r["status"] != "paid" and (r["due_date"] or "9999") < today
        status = "入金済" if r["status"] == "paid" else ("[red]期限超過[/red]" if overdue else "未入金")
        table.add_row(
            r["invoice_number"],
            r["issue_date"],
            r["buyer_name"],
            (r["subject"] or "")[:24],
            f"{int(float(r['total'])):,}",
            r["due_date"] or "",
            status,
        )
        total += int(float(r["total"]))
    console.print(table)
    console.print(f"[bold]{len(rows)} 件 / 合計 {total:,} 円[/bold]")


@app.command()
def paid(
    invoice_number: str = typer.Argument(..., help="請求書番号"),
    date: Optional[str] = typer.Option(None, "--date", "-d", help="入金日 (YYYY-MM-DD)"),
    config: Optional[Path] = ConfigOpt,
) -> None:
    """入金済みとして記録する。"""
    cfg = _load(config)
    paid_at = _parse_date(date)
    with Ledger(_ledger_path(cfg)) as ledger:
        if ledger.mark_paid(invoice_number, paid_at):
            console.print(f"[green]{invoice_number} を入金済みにしました（{paid_at}）[/green]")
        else:
            console.print(f"[red]見つかりません: {invoice_number}[/red]")
            raise typer.Exit(1)


@app.command()
def index(
    year: Optional[int] = typer.Option(None, "--year", "-y", help="対象年。省略で全期間"),
    out: Optional[Path] = typer.Option(None, "--out", "-o", help="出力先 CSV"),
    config: Optional[Path] = ConfigOpt,
) -> None:
    """電子帳簿保存法の検索要件を満たす索引簿 CSV を出力する。"""
    cfg = _load(config)
    target = out or (cfg.output.root / "_台帳" / f"索引簿_{year or '全期間'}.csv")
    with Ledger(_ledger_path(cfg)) as ledger:
        count = ledger.export_index_csv(target, year=year)
    console.print(f"[green]{count} 件を書き出しました:[/green] {target}")


@app.command()
def doctor(config: Optional[Path] = ConfigOpt) -> None:
    """設定と実行環境を点検する。"""
    import os

    ok = True

    try:
        cfg = config_mod.load(config)
        console.print(f"[green]✓[/green] 設定ファイル: {cfg.path}")
    except ConfigError as exc:
        console.print(f"[red]✗[/red] {exc}")
        raise typer.Exit(1) from exc

    problems = cfg.validate()
    if problems:
        ok = False
        console.print("[red]✗[/red] 設定の不足:")
        for p in problems:
            console.print(f"    ・{p}", markup=False)
    else:
        console.print("[green]✓[/green] 自社情報・登録番号・振込先")

    for note in cfg.warnings():
        # note の中の [company] を rich のスタイル指定と誤解させない
        console.print(f"[yellow]![/yellow] {escape(note)}")

    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        console.print("[green]✓[/green] Claude API の認証情報（環境変数）")
    elif (Path.home() / ".config" / "anthropic").exists():
        console.print("[green]✓[/green] Claude API の認証情報（ant のプロファイル）")
    else:
        ok = False
        console.print(
            "[red]✗[/red] Claude API の認証情報が見つかりません。\n"
            "    export ANTHROPIC_API_KEY=sk-ant-...  を設定してください。\n"
            "    キーの発行: https://console.anthropic.com/settings/keys"
        )

    try:
        console.print(f"[green]✓[/green] PDF 変換ブラウザ: {find_browser()}")
    except RenderError as exc:
        ok = False
        console.print(f"[red]✗[/red] {exc}")

    try:
        cfg.output.root.mkdir(parents=True, exist_ok=True)
        probe = cfg.output.root / ".seikyu_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        console.print(f"[green]✓[/green] 保存先に書き込めます: {cfg.output.root}")
    except OSError as exc:
        ok = False
        console.print(f"[red]✗[/red] 保存先に書き込めません（{cfg.output.root}）: {exc}")

    console.print(f"\n[dim]使用モデル: {cfg.api.model}（effort={cfg.api.effort}）[/dim]")
    if not ok:
        raise typer.Exit(1)
    console.print("\n[green bold]発行できる状態です。[/green bold]")


if __name__ == "__main__":
    app()
