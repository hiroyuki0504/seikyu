"""設定 (config/company.toml) の読み込み。

自社情報は請求書の必須記載事項なので、欠けていたら発行前に必ず止める。
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "config" / "company.toml"
EXAMPLE_CONFIG = REPO_ROOT / "config" / "company.example.toml"

# 適格請求書発行事業者の登録番号は T + 13 桁
REGISTRATION_RE = re.compile(r"^T\d{13}$")

# company.example.toml のダミー値。書き換え忘れのまま発行させない。
PLACEHOLDERS = {
    "株式会社〇〇",
    "T0000000000000",
    "000-0000",
    "東京都〇〇区〇〇 1-2-3",
    "〇〇銀行",
    "〇〇支店",
    "0000000",
    "カ)〇〇",
}


class ConfigError(RuntimeError):
    pass


@dataclass
class Company:
    name: str
    registration_number: str
    postal_code: str = ""
    address: str = ""
    building: str = ""
    tel: str = ""
    email: str = ""
    representative: str = ""
    seal_image: str = ""


@dataclass
class Bank:
    bank_name: str = ""
    branch_name: str = ""
    account_type: str = "普通"
    account_number: str = ""
    account_holder: str = ""
    note: str = ""

    @property
    def filled(self) -> bool:
        return bool(self.bank_name and self.account_number)


@dataclass
class InvoiceSettings:
    number_format: str = "INV-{year}-{seq:04d}"
    default_tax_rate: Decimal = Decimal("0.10")
    rounding: str = "floor"  # floor | ceil | round
    withholding: bool = False
    withholding_base: str = "net"  # net(税抜) | gross(税込)
    title: str = "請求書"


@dataclass
class PaymentSettings:
    closing_day: int = 31  # 31 は月末の意
    month_offset: int = 1
    payment_day: int = 31
    business_day_adjust: str = "none"  # none | before | after
    terms_label: str = ""


@dataclass
class OutputSettings:
    root: Path = Path.home() / "Documents" / "請求書"
    layout: str = "{year}/{year}-{month}"
    filename: str = "{invoice_number}_{buyer}_{date}_{total}"
    keep_source: bool = True


@dataclass
class WatchSettings:
    inbox: Path = Path.home() / "Documents" / "請求書" / "_発注書受信"
    processed: Path = Path.home() / "Documents" / "請求書" / "_処理済み"
    failed: Path = Path.home() / "Documents" / "請求書" / "_要確認"


@dataclass
class ApiSettings:
    model: str = "claude-opus-5"
    max_tokens: int = 16000
    effort: str = "high"


@dataclass
class Config:
    company: Company
    bank: Bank = field(default_factory=Bank)
    invoice: InvoiceSettings = field(default_factory=InvoiceSettings)
    payment: PaymentSettings = field(default_factory=PaymentSettings)
    output: OutputSettings = field(default_factory=OutputSettings)
    watch: WatchSettings = field(default_factory=WatchSettings)
    api: ApiSettings = field(default_factory=ApiSettings)
    path: Path | None = None

    def validate(self) -> list[str]:
        """発行前に潰しておくべき欠落を返す。"""
        problems: list[str] = []
        if not self.company.name:
            problems.append("[company] name（自社名）が未設定です")
        if not self.company.registration_number:
            problems.append(
                "[company] registration_number（適格請求書発行事業者の登録番号）が未設定です"
            )
        elif not REGISTRATION_RE.match(self.company.registration_number):
            problems.append(
                f"[company] registration_number が T+13桁の形式ではありません: "
                f"{self.company.registration_number}"
            )
        # 自社住所は適格請求書の必須記載事項ではないので、空でも止めない（warnings 参照）
        if not self.bank.filled:
            problems.append("[bank] 振込先（bank_name / account_number）が未設定です")
        if self.invoice.rounding not in ("floor", "ceil", "round"):
            problems.append(
                f"[invoice] rounding は floor / ceil / round のいずれか: {self.invoice.rounding}"
            )
        if self.invoice.withholding_base not in ("net", "gross"):
            problems.append("[invoice] withholding_base は net / gross のいずれか")
        if self.payment.business_day_adjust not in ("none", "before", "after"):
            problems.append("[payment] business_day_adjust は none / before / after のいずれか")
        if self.api.effort not in ("low", "medium", "high", "xhigh", "max"):
            problems.append(
                f"[api] effort は low / medium / high / xhigh / max のいずれか: {self.api.effort}"
            )

        untouched = [
            label
            for label, value in (
                ("[company] name", self.company.name),
                ("[company] registration_number", self.company.registration_number),
                ("[company] postal_code", self.company.postal_code),
                ("[company] address", self.company.address),
                ("[bank] bank_name", self.bank.bank_name),
                ("[bank] branch_name", self.bank.branch_name),
                ("[bank] account_number", self.bank.account_number),
                ("[bank] account_holder", self.bank.account_holder),
            )
            if value in PLACEHOLDERS
        ]
        if untouched:
            problems.append(
                "ひな形のダミー値のままの項目があります（"
                + " / ".join(untouched)
                + "）。実際の値に書き換えてください。"
            )
        return problems

    def warnings(self) -> list[str]:
        """発行は止めないが、伝えておきたいこと。"""
        notes: list[str] = []
        if not self.company.address:
            notes.append(
                "[company] address が空です。自社住所は適格請求書の必須記載事項ではないので"
                "このまま発行できます（請求書にも出しません）。"
                "取引先から記載を求められたら埋めてください。"
            )
        if not self.company.tel and not self.company.email:
            notes.append(
                "[company] tel / email がどちらも空です。請求書に問い合わせ先が載りません。"
            )
        return notes


def _expand(value: str) -> Path:
    return Path(os.path.expanduser(str(value))).expanduser()


def load(path: Path | str | None = None) -> Config:
    cfg_path = Path(path) if path else DEFAULT_CONFIG
    if not cfg_path.exists():
        raise ConfigError(
            f"設定ファイルがありません: {cfg_path}\n"
            f"  cp {EXAMPLE_CONFIG} {cfg_path}\n"
            f"を実行して、自社情報・登録番号・振込先を記入してください。"
        )
    with cfg_path.open("rb") as fh:
        raw: dict[str, Any] = tomllib.load(fh)

    c = raw.get("company", {})
    company = Company(
        name=str(c.get("name", "")).strip(),
        registration_number=str(c.get("registration_number", "")).strip().upper(),
        postal_code=str(c.get("postal_code", "")).strip(),
        address=str(c.get("address", "")).strip(),
        building=str(c.get("building", "")).strip(),
        tel=str(c.get("tel", "")).strip(),
        email=str(c.get("email", "")).strip(),
        representative=str(c.get("representative", "")).strip(),
        seal_image=str(c.get("seal_image", "")).strip(),
    )

    b = raw.get("bank", {})
    bank = Bank(
        bank_name=str(b.get("bank_name", "")).strip(),
        branch_name=str(b.get("branch_name", "")).strip(),
        account_type=str(b.get("account_type", "普通")).strip(),
        account_number=str(b.get("account_number", "")).strip(),
        account_holder=str(b.get("account_holder", "")).strip(),
        note=str(b.get("note", "")).strip(),
    )

    i = raw.get("invoice", {})
    invoice = InvoiceSettings(
        number_format=str(i.get("number_format", "INV-{year}-{seq:04d}")),
        default_tax_rate=Decimal(str(i.get("default_tax_rate", "0.10"))),
        rounding=str(i.get("rounding", "floor")),
        withholding=bool(i.get("withholding", False)),
        withholding_base=str(i.get("withholding_base", "net")),
        title=str(i.get("title", "請求書")),
    )

    p = raw.get("payment", {})
    payment = PaymentSettings(
        closing_day=int(p.get("closing_day", 31)),
        month_offset=int(p.get("month_offset", 1)),
        payment_day=int(p.get("payment_day", 31)),
        business_day_adjust=str(p.get("business_day_adjust", "none")),
        terms_label=str(p.get("terms_label", "")).strip(),
    )

    o = raw.get("output", {})
    output = OutputSettings(
        root=_expand(o.get("root", Path.home() / "Documents" / "請求書")),
        layout=str(o.get("layout", "{year}/{year}-{month}")),
        filename=str(o.get("filename", "{invoice_number}_{buyer}_{date}_{total}")),
        keep_source=bool(o.get("keep_source", True)),
    )

    w = raw.get("watch", {})
    watch = WatchSettings(
        inbox=_expand(w.get("inbox", output.root / "_発注書受信")),
        processed=_expand(w.get("processed", output.root / "_処理済み")),
        failed=_expand(w.get("failed", output.root / "_要確認")),
    )

    a = raw.get("api", {})
    api = ApiSettings(
        model=str(a.get("model", "claude-opus-5")),
        max_tokens=int(a.get("max_tokens", 16000)),
        effort=str(a.get("effort", "high")),
    )

    return Config(
        company=company,
        bank=bank,
        invoice=invoice,
        payment=payment,
        output=output,
        watch=watch,
        api=api,
        path=cfg_path,
    )
