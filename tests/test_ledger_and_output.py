from __future__ import annotations

import datetime as dt
from decimal import Decimal

from conftest import item, make_order

from seikyu.ledger import Ledger
from seikyu.naming import output_path, safe_component, unique_path
from seikyu.render import render_html
from seikyu.tax import build_invoice


def test_numbering_is_sequential_per_year(tmp_path):
    with Ledger(tmp_path / "l.db") as ledger:
        assert ledger.reserve_number(2026, "INV-{year}-{seq:04d}") == ("INV-2026-0001", 1)
        assert ledger.reserve_number(2026, "INV-{year}-{seq:04d}") == ("INV-2026-0002", 2)
        # 年が変わったら 1 に戻る
        assert ledger.reserve_number(2027, "INV-{year}-{seq:04d}") == ("INV-2027-0001", 1)
        # 元の年は続きから
        assert ledger.reserve_number(2026, "INV-{year}-{seq:04d}") == ("INV-2026-0003", 3)


def test_peek_does_not_consume_a_number(tmp_path):
    with Ledger(tmp_path / "l.db") as ledger:
        assert ledger.peek_number(2026, "INV-{year}-{seq:04d}") == "INV-2026-0001"
        assert ledger.peek_number(2026, "INV-{year}-{seq:04d}") == "INV-2026-0001"
        assert ledger.reserve_number(2026, "INV-{year}-{seq:04d}")[0] == "INV-2026-0001"


def test_record_and_duplicate_detection(tmp_path, cfg):
    order = make_order([item("制作費", "1", "300000")], source_sha256="abc123")
    inv = build_invoice(order, "INV-2026-0001", dt.date(2026, 8, 18), cfg)

    with Ledger(tmp_path / "l.db") as ledger:
        assert ledger.find_by_source("abc123") == []
        ledger.record(inv, tmp_path / "out.pdf", 2026, 1)
        dups = ledger.find_by_source("abc123")
        assert len(dups) == 1
        assert dups[0]["invoice_number"] == "INV-2026-0001"


def test_mark_paid_and_list(tmp_path, cfg):
    order = make_order([item("制作費", "1", "300000")])
    inv = build_invoice(order, "INV-2026-0001", dt.date(2026, 8, 18), cfg)

    with Ledger(tmp_path / "l.db") as ledger:
        ledger.record(inv, tmp_path / "out.pdf", 2026, 1)
        assert len(ledger.list_invoices(unpaid_only=True)) == 1
        assert ledger.mark_paid("INV-2026-0001", dt.date(2026, 9, 30)) is True
        assert ledger.list_invoices(unpaid_only=True) == []
        assert ledger.mark_paid("INV-9999-9999", dt.date(2026, 9, 30)) is False


def test_index_csv_has_search_requirements(tmp_path, cfg):
    order = make_order([item("制作費", "1", "300000")])
    inv = build_invoice(order, "INV-2026-0001", dt.date(2026, 8, 18), cfg)
    out = tmp_path / "索引簿.csv"

    with Ledger(tmp_path / "l.db") as ledger:
        ledger.record(inv, tmp_path / "out.pdf", 2026, 1)
        assert ledger.export_index_csv(out, year=2026) == 1

    text = out.read_text(encoding="utf-8-sig")
    # 電子帳簿保存法の検索要件（取引年月日・取引金額・取引先）
    assert "取引年月日" in text and "取引金額" in text and "取引先" in text
    assert "2026-08-18" in text
    assert "330000" in text
    assert "株式会社ABC" in text


def test_safe_component_strips_path_separators():
    assert safe_component("株式会社A/B") == "株式会社AB"
    assert safe_component("a:b*c?d") == "abcd"
    assert safe_component("  ") == "不明"


def test_output_path_layout(cfg):
    order = make_order([item("制作費", "1", "300000")])
    inv = build_invoice(order, "INV-2026-0001", dt.date(2026, 8, 18), cfg)
    path = output_path(inv, cfg)
    assert path.parent.name == "2026-08"
    assert path.parent.parent.name == "2026"
    assert path.name == "INV-2026-0001_株式会社ABC_20260818_330000.pdf"


def test_unique_path_avoids_overwrite(tmp_path):
    first = tmp_path / "a.pdf"
    first.write_bytes(b"x")
    assert unique_path(first).name == "a_2.pdf"


def test_render_html_contains_mandatory_fields(cfg):
    order = make_order(
        [
            item("制作費", "1", "300000", "0.10"),
            item("茶菓子", "10", "500", "0.08"),
        ]
    )
    inv = build_invoice(order, "INV-2026-0001", dt.date(2026, 8, 18), cfg)
    html = render_html(inv, cfg)

    # 適格請求書の必須記載事項
    assert "株式会社テスト" in html          # 1. 発行事業者の名称
    assert "T1234567890123" in html          # 1. 登録番号
    assert "2026年8月18日" in html           # 2. 取引年月日
    assert "制作費" in html                  # 3. 取引内容
    assert "※" in html                       # 3. 軽減税率対象である旨
    assert "10%対象" in html and "8%対象" in html   # 4. 税率ごとに区分した対価
    assert "30,000" in html and "400" in html       # 5. 税率ごとの消費税額
    assert "株式会社ABC" in html             # 6. 交付を受ける事業者の名称
    assert "335,400" in html                 # 合計
    assert "テスト銀行" in html              # 振込先


def test_render_html_hides_withholding_when_zero(cfg):
    order = make_order([item("制作費", "1", "300000")])
    inv = build_invoice(order, "INV-2026-0001", dt.date(2026, 8, 18), cfg)
    assert "源泉徴収" not in render_html(inv, cfg)

    order2 = make_order([item("原稿料", "1", "300000")], withholding_tax=True)
    inv2 = build_invoice(order2, "INV-2026-0002", dt.date(2026, 8, 18), cfg)
    html2 = render_html(inv2, cfg)
    assert "源泉徴収" in html2
    assert "30,630" in html2


def test_render_html_omits_rate_column_for_single_rate(cfg):
    order = make_order([item("制作費", "1", "300000")])
    html = render_html(build_invoice(order, "X", dt.date(2026, 8, 18), cfg), cfg)
    assert Decimal("1")  # sanity
    assert "軽減税率（8%）対象" not in html


def test_render_html_without_company_address(cfg):
    """自社住所を空にしても、請求書は成立し余計な空行も出ない。"""
    cfg.company.address = ""
    cfg.company.postal_code = ""
    order = make_order([item("制作費", "1", "300000")])
    html = render_html(build_invoice(order, "INV-2026-0001", dt.date(2026, 8, 18), cfg), cfg)

    assert "株式会社テスト" in html
    assert "T1234567890123" in html
    assert "〒" not in html.split('class="issuer"')[1].split("</div>")[0]
