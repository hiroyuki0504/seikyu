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
