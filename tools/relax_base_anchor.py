"""给 HFC 的 patcher 加锚点容忍：允许 Hermes 给 _deliver_attachments 增加正交关键字参数。

改两处 _find_*_decomposed_base_patch_locations 里的 attachments 锚点比对，
让它们接受“有无 record_delivery=...”两种形态。幂等：已打过就跳过。
"""

from __future__ import annotations

import pathlib
import sys

PATH = pathlib.Path("hermes_feishu_card/install/patcher.py")

HELPER = '''    def exact_any(scope, sources):
        """Accept any known-good spelling of one anchored statement.

        Hermes may add orthogonal keyword arguments to a call we anchor on — e.g.
        ``record_delivery=_record_delivery`` on ``_deliver_attachments``. A drift like
        that is compatible, so accept either spelling instead of failing the install.
        """
        for source in sources:
            expected = ast.parse(source).body[0]
            matches = [n for n in ast.walk(scope) if ast.dump(n) == ast.dump(expected)]
            if len(matches) == 1:
                return matches[0]
        raise ValueError(error)


'''

# 每个函数里 `def exact(...)` 的原文（两处格式不同，各自唯一）
EXACT_A = """    def exact(scope, source):
        expected = ast.parse(source).body[0]
        return _unique_exact_base_node(ast.walk(scope), lambda n: ast.dump(n) == ast.dump(expected))
"""

EXACT_B = """    def exact(scope, source):
        expected = ast.parse(source).body[0]
        return _unique_exact_base_node(
            ast.walk(scope), lambda node: ast.dump(node) == ast.dump(expected)
        )
"""

OLD_ATTACH = "await self._deliver_attachments(event, extracted, _final_thread_metadata, anything_sent=delivery_attempted or _tts_caption_delivered)"
NEW_ATTACH = OLD_ATTACH[:-1] + ", record_delivery=_record_delivery)"

CALL_A_OLD = f"""    attachments = exact(process, '{OLD_ATTACH}')
"""
CALL_A_NEW = f"""    attachments = exact_any(process, (
        '{OLD_ATTACH}',
        '{NEW_ATTACH}',
    ))
"""

CALL_B_OLD = f"""    attachments = exact(
        process,
        "{OLD_ATTACH}",
    )
"""
CALL_B_NEW = f"""    attachments = exact_any(
        process,
        (
            "{OLD_ATTACH}",
            "{NEW_ATTACH}",
        ),
    )
"""


def apply(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n == 0 and text.count(new) > 0:
        print(f"  [skip] {label}: 已打过")
        return text
    if n != 1:
        sys.exit(f"  [FAIL] {label}: 找到 {n} 处（期望 1）")
    print(f"  [ok]   {label}")
    return text.replace(old, new, 1)


def main() -> None:
    text = PATH.read_text(encoding="utf-8")
    print("打补丁：")
    text = apply(text, EXACT_A, EXACT_A + HELPER, "函数A 插入 exact_any")
    text = apply(text, EXACT_B, EXACT_B + "\n" + HELPER, "函数B 插入 exact_any")
    text = apply(text, CALL_A_OLD, CALL_A_NEW, "函数A attachments 锚点放宽")
    text = apply(text, CALL_B_OLD, CALL_B_NEW, "函数B attachments 锚点放宽")
    PATH.write_text(text, encoding="utf-8")
    print("已写入", PATH)


if __name__ == "__main__":
    main()
