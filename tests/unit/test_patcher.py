import ast

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


@pytest.mark.parametrize(
    "content",
    [
        """
async def _handle_message_with_agent(message):
    note = "# HERMES_FEISHU_CARD_PATCH_BEGIN"
    return message
""",
        """
# # HERMES_FEISHU_CARD_PATCH_BEGIN
async def _handle_message_with_agent(message):
    return message
""",
    ],
)
def test_marker_text_in_string_or_comment_fails_closed(content):
    with pytest.raises(ValueError, match="corrupt patch markers"):
        patcher.apply_patch(content)

    with pytest.raises(ValueError, match="corrupt patch markers"):
        patcher.remove_patch(content)


@pytest.mark.parametrize(
    "content",
    [
        '''
"""
# HERMES_FEISHU_CARD_PATCH_BEGIN
try:
    pass
except Exception:
    pass
# HERMES_FEISHU_CARD_PATCH_END
"""
async def _handle_message_with_agent(message):
    return message
''',
        '''
# HERMES_FEISHU_CARD_PATCH_BEGIN
# try:
#     pass
# except Exception:
#     pass
# HERMES_FEISHU_CARD_PATCH_END
async def _handle_message_with_agent(message):
    return message
''',
    ],
)
def test_complete_marker_shape_outside_handler_fails_closed(content):
    with pytest.raises(ValueError, match="corrupt patch markers"):
        patcher.apply_patch(content)

    with pytest.raises(ValueError, match="corrupt patch markers"):
        patcher.remove_patch(content)


def test_multiple_unrelated_markers_raise():
    content = f"""
async def _handle_message_with_agent(message):
    {patcher.PATCH_BEGIN}
    return message

async def other(message):
    {patcher.PATCH_END}
    return message
"""

    with pytest.raises(ValueError, match="corrupt patch markers"):
        patcher.apply_patch(content)

    with pytest.raises(ValueError, match="corrupt patch markers"):
        patcher.remove_patch(content)


def test_module_level_triple_quoted_handler_name_is_not_patched():
    content = '''
"""
async def _handle_message_with_agent(message):
    return message
"""
def unrelated():
    return None
'''

    with pytest.raises(ValueError, match="safe handler"):
        patcher.apply_patch(content)


@pytest.mark.parametrize(
    "content",
    [
        """
def outer():
    async def _handle_message_with_agent(message):
        return message
""",
        """
def outer():
    class Gateway:
        async def _handle_message_with_agent(self, message):
            return message
""",
    ],
)
def test_nested_handler_locations_are_not_patched(content):
    with pytest.raises(ValueError, match="safe handler"):
        patcher.apply_patch(content)


def test_non_async_and_prefixed_handler_names_are_not_patched():
    for content in (
        "def _handle_message_with_agent(message):\n    return message\n",
        "async def prefix_handle_message_with_agent(message):\n    return message\n",
    ):
        with pytest.raises(ValueError, match="safe handler"):
            patcher.apply_patch(content)


def test_crlf_patch_does_not_insert_bare_lf():
    content = (
        "async def _handle_message_with_agent(message):\r\n"
        "    return message\r\n"
    )

    result = patcher.apply_patch(content)

    assert "\n" in result
    assert "\n" not in result.replace("\r\n", "")


def test_apply_remove_round_trip_preserves_parseable_body():
    content = (
        "VALUE = 1\n\n"
        "async def _handle_message_with_agent(message):\n"
        "    original = message\n"
        "    return original\n"
    )

    patched = patcher.apply_patch(content)
    restored = patcher.remove_patch(patched)

    ast.parse(patched)
    ast.parse(restored)
    assert restored == content


def test_apply_remove_round_trip_preserves_missing_final_newline():
    content = (
        "async def _handle_message_with_agent(message):\n"
        "    return message"
    )

    patched = patcher.apply_patch(content)
    restored = patcher.remove_patch(patched)

    assert restored == content


def test_apply_patch_handles_module_level_multiline_signature():
    content = (
        "async def _handle_message_with_agent(\n"
        "    message,\n"
        "):\n"
        "    return message\n"
    )

    patched = patcher.apply_patch(content)
    restored = patcher.remove_patch(patched)

    ast.parse(patched)
    assert "):\n    # HERMES_FEISHU_CARD_PATCH_BEGIN\n" in patched
    assert "    # HERMES_FEISHU_CARD_PATCH_END\n    return message\n" in patched
    assert restored == content


def test_apply_patch_handles_class_method_multiline_signature():
    content = (
        "class Gateway:\n"
        "    async def _handle_message_with_agent(\n"
        "        self,\n"
        "        message,\n"
        "    ):\n"
        "        return message\n"
    )

    patched = patcher.apply_patch(content)
    restored = patcher.remove_patch(patched)

    ast.parse(patched)
    assert "    ):\n        # HERMES_FEISHU_CARD_PATCH_BEGIN\n" in patched
    assert "        # HERMES_FEISHU_CARD_PATCH_END\n        return message\n" in patched
    assert restored == content


def test_apply_patch_preserves_module_level_handler_docstring():
    content = (
        "async def _handle_message_with_agent(message):\n"
        "    \"\"\"Keep this docstring.\"\"\"\n"
        "    return message\n"
    )

    patched = patcher.apply_patch(content)
    restored = patcher.remove_patch(patched)
    handler = ast.parse(patched).body[0]

    assert ast.get_docstring(handler) == "Keep this docstring."
    assert "\"\"\"Keep this docstring.\"\"\"\n    # HERMES_FEISHU_CARD_PATCH_BEGIN\n" in patched
    assert "    # HERMES_FEISHU_CARD_PATCH_END\n    return message\n" in patched
    assert patcher.apply_patch(patched) == patched
    assert restored == content


def test_apply_patch_preserves_class_method_docstring():
    content = (
        "class Gateway:\n"
        "    async def _handle_message_with_agent(self, message):\n"
        "        \"\"\"Keep method docstring.\"\"\"\n"
        "        return message\n"
    )

    patched = patcher.apply_patch(content)
    restored = patcher.remove_patch(patched)
    method = ast.parse(patched).body[0].body[0]

    assert ast.get_docstring(method) == "Keep method docstring."
    assert "\"\"\"Keep method docstring.\"\"\"\n        # HERMES_FEISHU_CARD_PATCH_BEGIN\n" in patched
    assert "        # HERMES_FEISHU_CARD_PATCH_END\n        return message\n" in patched
    assert patcher.apply_patch(patched) == patched
    assert restored == content


def test_apply_patch_preserves_tab_indented_module_handler_prefix():
    content = (
        "async def _handle_message_with_agent(message):\n"
        "\treturn message\n"
    )

    patched = patcher.apply_patch(content)
    restored = patcher.remove_patch(patched)

    ast.parse(patched)
    assert f"\t{patcher.PATCH_BEGIN}\n" in patched
    assert "\ttry:\n" in patched
    assert "\t\tpass\n" in patched
    assert "    # HERMES_FEISHU_CARD_PATCH_BEGIN" not in patched
    assert restored == content


def test_apply_patch_preserves_tab_indented_class_method_prefix():
    content = (
        "class Gateway:\n"
        "\tasync def _handle_message_with_agent(self, message):\n"
        "\t\treturn message\n"
    )

    patched = patcher.apply_patch(content)
    restored = patcher.remove_patch(patched)

    ast.parse(patched)
    assert f"\t\t{patcher.PATCH_BEGIN}\n" in patched
    assert "\t\ttry:\n" in patched
    assert "\t\t\tpass\n" in patched
    assert restored == content


def test_apply_patch_handles_docstring_only_handler():
    content = (
        "async def _handle_message_with_agent(message):\n"
        "    \"\"\"Only documentation.\"\"\"\n"
    )

    patched = patcher.apply_patch(content)
    restored = patcher.remove_patch(patched)
    handler = ast.parse(patched).body[0]

    assert ast.get_docstring(handler) == "Only documentation."
    assert "\"\"\"Only documentation.\"\"\"\n    # HERMES_FEISHU_CARD_PATCH_BEGIN\n" in patched
    assert patcher.apply_patch(patched) == patched
    assert restored == content


def test_apply_patch_handles_docstring_only_handler_without_final_newline():
    content = (
        "async def _handle_message_with_agent(message):\n"
        "    \"\"\"Only documentation.\"\"\""
    )

    patched = patcher.apply_patch(content)
    restored = patcher.remove_patch(patched)
    handler = ast.parse(patched).body[0]

    assert ast.get_docstring(handler) == "Only documentation."
    assert "\"\"\"Only documentation.\"\"\"\n    # HERMES_FEISHU_CARD_NO_FINAL_NEWLINE\n" in patched
    assert patcher.apply_patch(patched) == patched
    assert restored == content


def test_apply_remove_preserves_docstring_blank_line_before_return():
    content = (
        "async def _handle_message_with_agent(message):\n"
        "    \"\"\"Keep this docstring.\"\"\"\n"
        "\n"
        "    return message\n"
    )

    patched = patcher.apply_patch(content)
    restored = patcher.remove_patch(patched)

    assert ast.get_docstring(ast.parse(patched).body[0]) == "Keep this docstring."
    assert restored == content


def test_apply_patch_rejects_module_level_one_line_handler():
    content = "async def _handle_message_with_agent(message): pass\n"

    with pytest.raises(ValueError, match="safe handler"):
        patcher.apply_patch(content)


def test_apply_patch_rejects_class_method_one_line_handler():
    content = (
        "class Gateway:\n"
        "    async def _handle_message_with_agent(self, message): pass\n"
    )

    with pytest.raises(ValueError, match="safe handler"):
        patcher.apply_patch(content)


def test_user_sentinel_before_valid_looking_hook_is_not_owned():
    content = f"""
async def _handle_message_with_agent(message):
    # HERMES_FEISHU_CARD_NO_FINAL_NEWLINE
    {patcher.PATCH_BEGIN}
    try:
        pass
    except Exception:
        pass
    {patcher.PATCH_END}
    return message
"""

    with pytest.raises(ValueError, match="corrupt patch markers"):
        patcher.apply_patch(content)

    with pytest.raises(ValueError, match="corrupt patch markers"):
        patcher.remove_patch(content)


def test_user_comment_before_sentinel_is_not_owned():
    content = f"""
async def _handle_message_with_agent(message):
    \"\"\"Only documentation.\"\"\"
    # user comment
    # HERMES_FEISHU_CARD_NO_FINAL_NEWLINE
    {patcher.PATCH_BEGIN}
    try:
        pass
    except Exception:
        pass
    {patcher.PATCH_END}
"""

    with pytest.raises(ValueError, match="corrupt patch markers"):
        patcher.apply_patch(content)

    with pytest.raises(ValueError, match="corrupt patch markers"):
        patcher.remove_patch(content)


def test_user_comment_between_sentinel_and_marker_is_not_owned():
    content = f"""
async def _handle_message_with_agent(message):
    \"\"\"Only documentation.\"\"\"
    # HERMES_FEISHU_CARD_NO_FINAL_NEWLINE
    # user comment
    {patcher.PATCH_BEGIN}
    try:
        pass
    except Exception:
        pass
    {patcher.PATCH_END}
"""

    with pytest.raises(ValueError, match="corrupt patch markers"):
        patcher.apply_patch(content)

    with pytest.raises(ValueError, match="corrupt patch markers"):
        patcher.remove_patch(content)


def test_isolated_no_final_newline_sentinel_is_rejected():
    content = """
async def _handle_message_with_agent(message):
    # HERMES_FEISHU_CARD_NO_FINAL_NEWLINE
    return message
"""

    with pytest.raises(ValueError, match="corrupt patch markers"):
        patcher.apply_patch(content)

    with pytest.raises(ValueError, match="corrupt patch markers"):
        patcher.remove_patch(content)


def test_remove_rejects_marker_block_with_wrong_shape():
    content = f"""
async def _handle_message_with_agent(message):
    {patcher.PATCH_BEGIN}
    print("not owned")
    {patcher.PATCH_END}
    return message
"""

    with pytest.raises(ValueError, match="corrupt patch markers"):
        patcher.apply_patch(content)

    with pytest.raises(ValueError, match="corrupt patch markers"):
        patcher.remove_patch(content)


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
