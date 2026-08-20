"""消費税・源泉徴収・支払期限の計算。

適格請求書（インボイス）の要点:
- 税率ごとに区分して対価の額を合計し、税率ごとに区分した消費税額を記載する。
- 消費税額の端数処理は「一の適格請求書につき、税率ごとに 1 回」まで。
  明細行ごとに端数処理してから足し上げるのは認められない。
"""

from __future__ import annotations

import calendar
import datetime as dt
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_HALF_UP, Decimal
from itertools import groupby

from .config import Config
from .models import Invoice, PurchaseOrder, TaxBucket

YEN = Decimal("1")

# 源泉徴収（報酬・料金等）。100万円を境に税率が変わる。
WITHHOLDING_THRESHOLD = Decimal("1000000")
WITHHOLDING_RATE_LOW = Decimal("0.1021")
WITHHOLDING_RATE_HIGH = Decimal("0.2042")
WITHHOLDING_FIXED = Decimal("102100")

_ROUNDING = {
    "floor": ROUND_DOWN,
    "ceil": ROUND_CEILING,
    "round": ROUND_HALF_UP,
}


def round_yen(value: Decimal, mode: str) -> Decimal:
    """円未満を設定どおりに処理する。"""
    return value.quantize(YEN, rounding=_ROUNDING[mode])


def line_amount(item, mode: str) -> Decimal:
    """明細行の表示金額（税抜・円単位）。"""
    return round_yen(item.amount, mode)


def build_buckets(order: PurchaseOrder, mode: str) -> list[TaxBucket]:
    """税率ごとに区分集計する。消費税額の丸めはここで税率ごとに 1 回だけ行う。"""
    items = sorted(order.items, key=lambda i: -i.tax_rate)
    buckets: list[TaxBucket] = []
    for rate, group in groupby(items, key=lambda i: i.tax_rate):
        # 明細の表示金額を積み上げる。表示と合計を必ず一致させるため。
        net = sum((line_amount(i, mode) for i in group), Decimal("0"))
        tax = round_yen(net * rate, mode)
        buckets.append(TaxBucket(rate=rate, net=net, tax=tax))
    return buckets


def withholding_amount(base: Decimal) -> Decimal:
    """源泉徴収税額（復興特別所得税込み）。円未満切捨て。"""
    if base <= 0:
        return Decimal("0")
    if base <= WITHHOLDING_THRESHOLD:
        raw = base * WITHHOLDING_RATE_LOW
    else:
        raw = (base - WITHHOLDING_THRESHOLD) * WITHHOLDING_RATE_HIGH + WITHHOLDING_FIXED
    return raw.quantize(YEN, rounding=ROUND_DOWN)


def _month_end(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _add_months(d: dt.date, months: int) -> tuple[int, int]:
    total = (d.year * 12 + d.month - 1) + months
    return divmod(total, 12)[0], divmod(total, 12)[1] + 1


def closing_date(issue_date: dt.date, closing_day: int) -> dt.date:
    """締め日を求める。発行日が締め日を過ぎていれば翌月締め。"""
    eff = min(closing_day, _month_end(issue_date.year, issue_date.month))
    if issue_date.day <= eff:
        return dt.date(issue_date.year, issue_date.month, eff)
    year, month = _add_months(issue_date, 1)
    return dt.date(year, month, min(closing_day, _month_end(year, month)))


def _is_business_day(d: dt.date) -> bool:
    if d.weekday() >= 5:  # 土日
        return False
    try:
        import jpholiday
    except ImportError:  # 祝日データが無ければ土日のみで判定する
        return True
    return not jpholiday.is_holiday(d)


def adjust_business_day(d: dt.date, mode: str) -> dt.date:
    if mode == "none":
        return d
    step = dt.timedelta(days=-1 if mode == "before" else 1)
    guard = 0
    while not _is_business_day(d):
        d += step
        guard += 1
        if guard > 30:  # 年末年始でもここまで連続することはない
            break
    return d


def due_date(issue_date: dt.date, cfg: Config) -> dt.date:
    """支払期限。締め日を起点に「Nヶ月後の支払日」を求める。"""
    p = cfg.payment
    base = closing_date(issue_date, p.closing_day)
    year, month = _add_months(base, p.month_offset)
    day = min(p.payment_day, _month_end(year, month))
    return adjust_business_day(dt.date(year, month, day), p.business_day_adjust)


def payment_terms_label(cfg: Config) -> str:
    """支払条件の表示文字列。設定に terms_label があればそれを使う。"""
    if cfg.payment.terms_label:
        return cfg.payment.terms_label
    p = cfg.payment
    closing = "月末" if p.closing_day >= 31 else f"{p.closing_day}日"
    when = {0: "当月", 1: "翌月", 2: "翌々月"}.get(p.month_offset, f"{p.month_offset}ヶ月後")
    # 「翌月」+「月末」で "翌月月末" にならないよう、月末は末尾を「末」だけにする
    pay = f"{when}末" if p.payment_day >= 31 else f"{when}{p.payment_day}日"
    return f"{closing}締め {pay}払い"


def build_invoice(
    order: PurchaseOrder,
    invoice_number: str,
    issue_date: dt.date,
    cfg: Config,
) -> Invoice:
    """発注書の内容から請求書を組み立てる。"""
    mode = cfg.invoice.rounding
    buckets = build_buckets(order, mode)
    subtotal = sum((b.net for b in buckets), Decimal("0"))
    tax_total = sum((b.tax for b in buckets), Decimal("0"))

    apply_withholding = cfg.invoice.withholding or order.withholding_tax
    if apply_withholding:
        base = subtotal if cfg.invoice.withholding_base == "net" else subtotal + tax_total
        withholding = withholding_amount(base)
    else:
        withholding = Decimal("0")

    total = subtotal + tax_total - withholding

    return Invoice(
        invoice_number=invoice_number,
        issue_date=issue_date,
        due_date=due_date(issue_date, cfg),
        order=order,
        buckets=buckets,
        subtotal=subtotal,
        tax_total=tax_total,
        withholding=withholding,
        total=total,
    )


def verify_against_stated_total(invoice: Invoice) -> str | None:
    """発注書の印字合計と突き合わせる。ずれていれば警告文を返す。"""
    stated = invoice.order.stated_total
    if stated is None:
        return None
    computed = invoice.subtotal + invoice.tax_total
    diff = computed - stated
    if diff == 0:
        return None
    return (
        f"発注書の合計 {stated:,} 円 と、明細から計算した税込合計 {computed:,} 円 が "
        f"{abs(diff):,} 円 ずれています。明細を確認してください。"
    )
