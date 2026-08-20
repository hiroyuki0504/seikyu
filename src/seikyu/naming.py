"""保存先パスの組み立て。

電子帳簿保存法の検索要件（取引年月日・取引金額・取引先）をファイル名にも
入れておくと、台帳が壊れてもファイル名だけで探せる。
"""

from __future__ import annotations

import re
from pathlib import Path

from .config import Config
from .models import Invoice

# ファイル名に使えない文字。全角へ倒さず、単に落とす。
_UNSAFE = re.compile(r'[/\\:*?"<>|\x00-\x1f]')


def safe_component(text: str, limit: int = 40) -> str:
    cleaned = _UNSAFE.sub("", text).strip().strip(".")
    cleaned = re.sub(r"\s+", "", cleaned)
    return cleaned[:limit] or "不明"


def output_path(invoice: Invoice, cfg: Config) -> Path:
    fields = {
        "invoice_number": invoice.invoice_number,
        "buyer": safe_component(invoice.order.buyer.name),
        "date": invoice.issue_date.strftime("%Y%m%d"),
        "total": str(invoice.total),
        "year": invoice.issue_date.strftime("%Y"),
        "month": invoice.issue_date.strftime("%m"),
        "subject": safe_component(invoice.order.subject, 30),
    }
    directory = cfg.output.root
    for part in cfg.output.layout.format(**fields).split("/"):
        if part:
            directory = directory / safe_component(part, 60)
    return directory / (safe_component(cfg.output.filename.format(**fields), 120) + ".pdf")


def unique_path(path: Path) -> Path:
    """同名ファイルがあれば連番を足す。既存の請求書を上書きしないため。"""
    if not path.exists():
        return path
    for n in range(2, 100):
        candidate = path.with_name(f"{path.stem}_{n}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"保存先が埋まっています: {path}")
