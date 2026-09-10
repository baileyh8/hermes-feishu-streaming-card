"""Hermes 0.21.1 split-ledger patcher contracts."""

from pathlib import Path

import pytest

from hermes_feishu_card.install import patcher


FIXTURE = Path(__file__).parents[1] / "fixtures/hermes_split_ledger_base.py"


def test_split_ledger_install_is_idempotent_and_reversible() -> None:
    original = FIXTURE.read_text(encoding="utf-8")
    installed = patcher.apply_base_patch(original)
    assert "send_final_ledgered" in installed
    assert patcher.apply_base_patch(installed) == installed
    assert patcher.EXACT_BASE_FINAL_DELIVERY_PATCH_BEGIN in installed
    compile(installed, str(FIXTURE), "exec")
    assert patcher.remove_base_patch(installed) == original


def test_split_ledger_contract_rejects_the_old_inline_ledger_shape() -> None:
    original = FIXTURE.read_text(encoding="utf-8")
    broken = original.replace(
        "async def send_final_ledgered(",
        "async def send_final_ledgered_broken(",
    )
    with pytest.raises(ValueError, match="safe BasePlatformAdapter contract"):
        patcher.apply_base_patch(broken)
