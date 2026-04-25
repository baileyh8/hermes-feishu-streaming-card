import pytest

from hermes_feishu_card.install import patcher


def test_apply_patch_is_idempotent_for_existing_block():
    content = """
async def _handle_message_with_agent(message):
    # HERMES_FEISHU_CARD_PATCH_BEGIN
    try:
        pass
    except Exception:
        pass
    # HERMES_FEISHU_CARD_PATCH_END
    return message
"""

    assert patcher.apply_patch(content) == content


def test_remove_patch_removes_block_and_keeps_return_content():
    content = patcher.apply_patch(
        """
async def _handle_message_with_agent(message):
    return message
"""
    )

    result = patcher.remove_patch(content)

    assert patcher.PATCH_BEGIN not in result
    assert patcher.PATCH_END not in result
    assert "    return message\n" in result


def test_apply_patch_uses_class_method_body_indentation():
    content = """
class Gateway:
    async def _handle_message_with_agent(self, message):
        return message
"""

    result = patcher.apply_patch(content)

    assert f"        {patcher.PATCH_BEGIN}\n" in result
    assert "            pass\n" in result
    assert f"        {patcher.PATCH_END}\n" in result


def test_apply_patch_does_not_use_commented_handler_name():
    content = """
# async def _handle_message_with_agent(message):
def unrelated():
    return None
"""

    with pytest.raises(ValueError, match="safe handler"):
        patcher.apply_patch(content)


def test_half_marker_raises_for_apply_and_remove():
    content = f"""
async def _handle_message_with_agent(message):
    {patcher.PATCH_BEGIN}
    return message
"""

    with pytest.raises(ValueError, match="corrupt patch markers"):
        patcher.apply_patch(content)

    with pytest.raises(ValueError, match="corrupt patch markers"):
        patcher.remove_patch(content)


def test_remove_patch_raises_when_markers_are_reversed():
    content = f"""
async def _handle_message_with_agent(message):
    {patcher.PATCH_END}
    return message
    {patcher.PATCH_BEGIN}
"""

    with pytest.raises(ValueError, match="corrupt patch markers"):
        patcher.remove_patch(content)


def test_apply_patch_raises_when_no_handler_found():
    with pytest.raises(ValueError, match="safe handler"):
        patcher.apply_patch("def handle(message):\n    return message\n")
