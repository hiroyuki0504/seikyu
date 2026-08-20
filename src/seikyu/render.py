"""請求書 HTML → PDF。

PDF 化は macOS に入っている Chrome のヘッドレス印刷を使う。日本語フォントの
埋め込みや禁則処理をブラウザに任せられるので、PDF ライブラリを直接叩くより
見た目が安定する。
"""

from __future__ import annotations

import base64
import datetime as dt
import mimetypes
import shutil
import subprocess
import tempfile
import time
from decimal import Decimal
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import Config
from .models import Invoice
from .tax import line_amount, payment_terms_label

TEMPLATE_DIR = Path(__file__).parent / "templates"

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
]

WEEKDAYS = "月火水木金土日"


class RenderError(RuntimeError):
    pass


def yen(value: Decimal | int | str | None) -> str:
    """円の表示。整数なら小数点を出さない。"""
    if value is None or value == "":
        return ""
    d = Decimal(str(value))
    if d == d.to_integral_value():
        return f"{int(d):,}"
    return f"{d.normalize():,f}"


def quantity(value: Decimal) -> str:
    d = Decimal(str(value))
    if d == d.to_integral_value():
        return f"{int(d):,}"
    return f"{d.normalize():,f}"


def jp_date(value: dt.date | None) -> str:
    if value is None:
        return ""
    return f"{value.year}年{value.month}月{value.day}日（{WEEKDAYS[value.weekday()]}）"


def _data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.standard_b64encode(path.read_bytes()).decode()}"


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["yen"] = yen
    env.filters["qty"] = quantity
    env.filters["jpdate"] = jp_date
    return env


def render_html(invoice: Invoice, cfg: Config) -> str:
    mode = cfg.invoice.rounding
    rows = [
        {
            "item": item,
            "amount": line_amount(item, mode),
            "reduced": item.tax_rate == Decimal("0.08"),
        }
        for item in invoice.order.items
    ]

    seal = ""
    if cfg.company.seal_image:
        seal_path = Path(cfg.company.seal_image).expanduser()
        if seal_path.exists():
            seal = _data_uri(seal_path)

    return _env().get_template("invoice.html.j2").render(
        invoice=invoice,
        order=invoice.order,
        company=cfg.company,
        bank=cfg.bank,
        settings=cfg.invoice,
        rows=rows,
        seal=seal,
        terms=payment_terms_label(cfg),
        multi_rate=len(invoice.buckets) > 1 or invoice.has_reduced,
    )


def find_browser() -> str:
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    for name in ("google-chrome", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    raise RenderError(
        "PDF 変換に使えるブラウザが見つかりません。\n"
        "  Google Chrome をインストールするか、config で別の方法を指定してください。"
    )


def html_to_pdf(html: str, out_path: Path, timeout: float = 90.0) -> None:
    """HTML を PDF に変換する。

    macOS で Chrome が既に起動していると、ヘッドレスの `--print-to-pdf` は
    PDF を書き終えてもプロセスが終了しないことがある。終了を待つと必ず
    タイムアウトするので、出力ファイルが安定したのを見てこちらから止める。
    """
    browser = find_browser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()  # 前回の残骸を「完成」と誤検知しないため

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        src = tmp_dir / "invoice.html"
        src.write_text(html, encoding="utf-8")
        log_path = tmp_dir / "browser.log"
        cmd = [
            browser,
            "--headless",
            "--disable-gpu",
            # 起動中の Chrome と衝突しないよう、使い捨てプロファイルで動かす
            f"--user-data-dir={tmp_dir / 'profile'}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--no-pdf-header-footer",
            "--virtual-time-budget=4000",
            f"--print-to-pdf={out_path}",
            src.as_uri(),
        ]

        with log_path.open("wb") as log:
            proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
            try:
                _wait_for_pdf(proc, out_path, timeout)
            finally:
                _stop(proc)

        if not out_path.exists() or out_path.stat().st_size == 0:
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-800:]
            raise RenderError(
                f"PDF の生成に失敗しました（{browser}）。\n  出力: {tail.strip()}"
            )


def _wait_for_pdf(proc: subprocess.Popen, out_path: Path, timeout: float) -> None:
    """PDF のサイズが 2 周期変わらなくなったら書き終わったとみなす。"""
    deadline = time.monotonic() + timeout
    last_size = -1
    stable = 0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return  # 素直に終了したブラウザ
        if out_path.exists():
            size = out_path.stat().st_size
            if size > 0 and size == last_size:
                stable += 1
                if stable >= 2:
                    return
            else:
                stable = 0
            last_size = size
        time.sleep(0.25)


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def render_pdf(invoice: Invoice, cfg: Config, out_path: Path) -> Path:
    html_to_pdf(render_html(invoice, cfg), out_path)
    return out_path
