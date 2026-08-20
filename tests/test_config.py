from __future__ import annotations

import shutil

import pytest

from seikyu import config as config_mod
from seikyu.config import ConfigError


def test_example_template_is_rejected_until_filled_in(tmp_path):
    """ひな形のダミー値のままでは発行させない。"""
    target = tmp_path / "company.toml"
    shutil.copy2(config_mod.EXAMPLE_CONFIG, target)

    problems = config_mod.load(target).validate()
    assert any("ダミー値" in p for p in problems)


def test_filled_config_passes(tmp_path):
    target = tmp_path / "company.toml"
    text = config_mod.EXAMPLE_CONFIG.read_text(encoding="utf-8")
    for dummy, real in (
        ('name = "株式会社〇〇"', 'name = "株式会社リーディング"'),
        ('registration_number = "T0000000000000"', 'registration_number = "T1234567890123"'),
        ('postal_code = "000-0000"', 'postal_code = "150-0001"'),
        ('address = "東京都〇〇区〇〇 1-2-3"', 'address = "東京都渋谷区神宮前1-2-3"'),
        ('bank_name = "〇〇銀行"', 'bank_name = "みずほ銀行"'),
        ('branch_name = "〇〇支店"', 'branch_name = "渋谷支店"'),
        ('account_number = "0000000"', 'account_number = "1234567"'),
        ('account_holder = "カ)〇〇"', 'account_holder = "カ)リーディング"'),
    ):
        assert dummy in text, dummy
        text = text.replace(dummy, real, 1)
    target.write_text(text, encoding="utf-8")

    assert config_mod.load(target).validate() == []


def test_address_is_optional_but_warned(tmp_path):
    """自社住所は適格請求書の必須記載事項ではないので、空でも発行を止めない。"""
    target = tmp_path / "company.toml"
    target.write_text(
        '[company]\nname = "山本 太郎"\nregistration_number = "T1234567890123"\n'
        '[bank]\nbank_name = "みずほ銀行"\naccount_number = "1234567"\n',
        encoding="utf-8",
    )
    cfg = config_mod.load(target)
    assert cfg.validate() == []                       # 止めない
    assert any("address" in w for w in cfg.warnings())  # ただし知らせる


def test_bad_registration_number_is_rejected(tmp_path):
    target = tmp_path / "company.toml"
    target.write_text(
        '[company]\nname = "A"\nregistration_number = "1234567890123"\naddress = "東京"\n'
        '[bank]\nbank_name = "X銀行"\naccount_number = "1"\n',
        encoding="utf-8",
    )
    problems = config_mod.load(target).validate()
    assert any("T+13桁" in p for p in problems)


def test_missing_bank_is_rejected(tmp_path):
    target = tmp_path / "company.toml"
    target.write_text(
        '[company]\nname = "A"\nregistration_number = "T1234567890123"\naddress = "東京"\n',
        encoding="utf-8",
    )
    problems = config_mod.load(target).validate()
    assert any("振込先" in p for p in problems)


def test_missing_file_explains_how_to_create_it(tmp_path):
    with pytest.raises(ConfigError, match="設定ファイルがありません"):
        config_mod.load(tmp_path / "nope.toml")


def test_paths_are_expanded(tmp_path):
    target = tmp_path / "company.toml"
    target.write_text(
        '[company]\nname = "A"\nregistration_number = "T1234567890123"\naddress = "東京"\n'
        '[bank]\nbank_name = "X銀行"\naccount_number = "1"\n'
        '[output]\nroot = "~/請求書テスト"\n',
        encoding="utf-8",
    )
    cfg = config_mod.load(target)
    assert cfg.output.root.is_absolute()
    assert "~" not in str(cfg.output.root)


# ---- 免税事業者モード（インボイス未登録） -------------------------------


def _write(tmp_path, body: str):
    target = tmp_path / "company.toml"
    target.write_text(body, encoding="utf-8")
    return target


BANK = '[bank]\nbank_name = "三井住友銀行"\naccount_number = "1234567"\n'


def test_exempt_issuer_needs_no_registration_number(tmp_path):
    """免税事業者は適格請求書を発行できないので、登録番号なしで発行できる。"""
    target = _write(
        tmp_path,
        '[company]\nname = "山本 寛幸"\ntax_status = "exempt"\n' + BANK,
    )
    cfg = config_mod.load(target)
    assert cfg.company.is_exempt
    assert cfg.validate() == []


def test_exempt_issuer_with_registration_number_is_contradiction(tmp_path):
    """登録番号を持っているのに exempt を名乗るのは矛盾なので止める。"""
    target = _write(
        tmp_path,
        '[company]\nname = "山本 寛幸"\ntax_status = "exempt"\n'
        'registration_number = "T1234567890123"\n' + BANK,
    )
    problems = config_mod.load(target).validate()
    assert any("矛盾" in p or "設定されています" in p for p in problems)


def test_exempt_issuer_cannot_title_it_qualified_invoice(tmp_path):
    """免税事業者が「適格請求書」と題した書類を出さないよう止める。"""
    target = _write(
        tmp_path,
        '[company]\nname = "山本 寛幸"\ntax_status = "exempt"\n'
        + BANK
        + '[invoice]\ntitle = "適格請求書"\n',
    )
    problems = config_mod.load(target).validate()
    assert any("適格" in p for p in problems)


def test_taxable_issuer_still_requires_registration_number(tmp_path):
    """既定は課税事業者。登録番号がなければ従来どおり止め、exempt への誘導を出す。"""
    target = _write(tmp_path, '[company]\nname = "株式会社ABC"\n' + BANK)
    problems = config_mod.load(target).validate()
    assert any("registration_number" in p for p in problems)
    assert any("exempt" in p for p in problems)


def test_unknown_tax_status_is_rejected(tmp_path):
    target = _write(
        tmp_path, '[company]\nname = "山本 寛幸"\ntax_status = "免税"\n' + BANK
    )
    assert any("tax_status" in p for p in config_mod.load(target).validate())


def test_exempt_issuer_is_warned_when_charging_consumption_tax(tmp_path):
    """免税でも税率10%で出せるが、取引先の控除が制限される点を知らせる。"""
    target = _write(
        tmp_path,
        '[company]\nname = "山本 寛幸"\ntax_status = "exempt"\n'
        + BANK
        + "[invoice]\ndefault_tax_rate = 0.10\n",
    )
    cfg = config_mod.load(target)
    assert cfg.validate() == []
    assert any("経過措置" in w for w in cfg.warnings())


def test_exempt_issuer_with_zero_rate_has_no_tax_warning(tmp_path):
    target = _write(
        tmp_path,
        '[company]\nname = "山本 寛幸"\ntax_status = "exempt"\n'
        + BANK
        + "[invoice]\ndefault_tax_rate = 0.0\n",
    )
    cfg = config_mod.load(target)
    assert cfg.validate() == []
    assert not any("経過措置" in w for w in cfg.warnings())


def test_exempt_placeholder_check_ignores_registration_number(tmp_path):
    """exempt では登録番号が空なのが正しいので、ダミー値扱いにしない。"""
    target = _write(
        tmp_path,
        '[company]\nname = "山本 寛幸"\ntax_status = "exempt"\n' + BANK,
    )
    problems = config_mod.load(target).validate()
    assert not any("ダミー値" in p for p in problems)
