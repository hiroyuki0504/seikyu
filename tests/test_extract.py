"""抽出まわり。Claude API は呼ばず、入出力の配管だけを見る。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal

import pytest

from seikyu import extract as extract_mod
from seikyu.extract import ExtractionError, build_content, extract

# ---- content ブロックの組み立て -------------------------------------------


def test_csv_becomes_text_block(tmp_path):
    path = tmp_path / "po.csv"
    path.write_text("品名,数量,単価\n制作費,1,300000\n", encoding="utf-8")
    blocks = build_content(path)
    assert blocks[0]["type"] == "text"
    assert "制作費 | 1 | 300000" in blocks[0]["text"]


def test_cp932_csv_is_decoded(tmp_path):
    path = tmp_path / "po.csv"
    path.write_bytes("品名,金額\n制作費,300000\n".encode("cp932"))
    assert "制作費" in build_content(path)[0]["text"]


def test_xlsx_becomes_text_block(tmp_path):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "発注"
    ws.append(["品名", "数量", "単価"])
    ws.append(["制作費", 1, 300000])
    path = tmp_path / "po.xlsx"
    wb.save(path)

    text = build_content(path)[0]["text"]
    assert "シート: 発注" in text
    assert "制作費 | 1 | 300000" in text


def test_pdf_becomes_document_block(tmp_path):
    import pypdf

    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=595, height=842)
    path = tmp_path / "po.pdf"
    with path.open("wb") as fh:
        writer.write(fh)

    blocks = build_content(path)
    assert blocks[0]["type"] == "document"
    assert blocks[0]["source"]["media_type"] == "application/pdf"
    assert "\n" not in blocks[0]["source"]["data"]  # base64 に改行を混ぜない


def test_png_becomes_image_block(tmp_path):
    path = tmp_path / "po.png"
    path.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
            "890000000a49444154789c6300010000050001"
            "0d0a2db40000000049454e44ae426082"
        )
    )
    blocks = build_content(path)
    assert blocks[0]["type"] == "image"
    assert blocks[0]["source"]["media_type"] == "image/png"


def test_unsupported_suffix_is_rejected(tmp_path):
    path = tmp_path / "po.docx"
    path.write_bytes(b"x")
    with pytest.raises(ExtractionError, match="未対応"):
        build_content(path)


def test_legacy_xls_gives_actionable_message(tmp_path):
    path = tmp_path / "po.xls"
    path.write_bytes(b"x")
    with pytest.raises(ExtractionError, match="xlsx"):
        build_content(path)


def test_oversized_file_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(extract_mod, "MAX_REQUEST_BYTES", 10)
    path = tmp_path / "po.csv"
    path.write_text("a" * 100, encoding="utf-8")
    with pytest.raises(ExtractionError, match="大きすぎます"):
        build_content(path)


# ---- API レスポンスの取り込み ---------------------------------------------


@dataclass
class _Block:
    text: str
    type: str = "text"


@dataclass
class _Response:
    content: list
    stop_reason: str = "end_turn"
    stop_details: object = None


def _fake_client(payload: dict, stop_reason: str = "end_turn"):
    class _Messages:
        def create(self, **kwargs):
            _Messages.last_kwargs = kwargs
            return _Response(
                content=[_Block(json.dumps(payload, ensure_ascii=False))],
                stop_reason=stop_reason,
            )

    class _Client:
        messages = _Messages()

    return _Client()


GOOD_PAYLOAD = {
    "po_number": "PO-1",
    "order_date": "2026-08-01",
    "delivery_date": "",
    "subject": "制作一式",
    "buyer": {
        "name": "株式会社ABC",
        "department": "総務部",
        "contact": "山田",
        "postal_code": "100-0001",
        "address": "東京都",
    },
    "items": [
        {
            "name": "制作費",
            "quantity": "1",
            "unit": "式",
            "unit_price": "300000",
            "tax_rate": "10",
            "note": "",
        }
    ],
    "payment_terms": "",
    "notes": "",
    "stated_total": "330000",
    "withholding_tax": False,
    "confidence_notes": ["単価は税抜として読み取りました"],
}


def test_extract_maps_payload_and_records_provenance(tmp_path, cfg, monkeypatch):
    path = tmp_path / "po.csv"
    path.write_text("品名,金額\n制作費,300000\n", encoding="utf-8")
    monkeypatch.setattr(extract_mod, "_client", lambda cfg: _fake_client(GOOD_PAYLOAD))

    order = extract(path, cfg)
    assert order.buyer.name == "株式会社ABC"
    assert order.items[0].unit_price == Decimal("300000")
    assert order.items[0].tax_rate == Decimal("0.10")
    assert order.stated_total == Decimal("330000")
    assert order.source_file == str(path)
    assert len(order.source_sha256) == 64
    assert order.extraction_warnings == ["単価は税抜として読み取りました"]


def test_extract_sends_structured_output_config(tmp_path, cfg, monkeypatch):
    path = tmp_path / "po.csv"
    path.write_text("x,1\n", encoding="utf-8")
    client = _fake_client(GOOD_PAYLOAD)
    monkeypatch.setattr(extract_mod, "_client", lambda cfg: client)

    extract(path, cfg)
    kwargs = type(client).messages.last_kwargs
    assert kwargs["model"] == cfg.api.model
    assert kwargs["output_config"]["effort"] == cfg.api.effort
    assert kwargs["output_config"]["format"]["type"] == "json_schema"
    # スキーマは additionalProperties を閉じておく（構造化出力の要件）
    assert kwargs["output_config"]["format"]["schema"]["additionalProperties"] is False


def test_extract_rejects_empty_items(tmp_path, cfg, monkeypatch):
    path = tmp_path / "po.csv"
    path.write_text("x,1\n", encoding="utf-8")
    payload = {**GOOD_PAYLOAD, "items": []}
    monkeypatch.setattr(extract_mod, "_client", lambda cfg: _fake_client(payload))
    with pytest.raises(ExtractionError, match="明細を 1 行も"):
        extract(path, cfg)


def test_extract_warns_when_buyer_missing(tmp_path, cfg, monkeypatch):
    path = tmp_path / "po.csv"
    path.write_text("x,1\n", encoding="utf-8")
    payload = {**GOOD_PAYLOAD, "buyer": {**GOOD_PAYLOAD["buyer"], "name": ""}}
    monkeypatch.setattr(extract_mod, "_client", lambda cfg: _fake_client(payload))
    order = extract(path, cfg)
    assert any("請求先" in w for w in order.extraction_warnings)


def test_extract_reports_truncated_output(tmp_path, cfg, monkeypatch):
    path = tmp_path / "po.csv"
    path.write_text("x,1\n", encoding="utf-8")
    monkeypatch.setattr(
        extract_mod, "_client", lambda cfg: _fake_client(GOOD_PAYLOAD, "max_tokens")
    )
    with pytest.raises(ExtractionError, match="max_tokens"):
        extract(path, cfg)
