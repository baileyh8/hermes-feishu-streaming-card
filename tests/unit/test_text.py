from hermes_feishu_card.text import normalize_stream_text, should_flush_text


def test_normalize_removes_think_tags():
    raw = "<think>我在分析</think>\n最终不会出现标签"
    assert normalize_stream_text(raw) == "我在分析\n最终不会出现标签"


def test_flushes_on_chinese_sentence_end():
    assert should_flush_text("我先分析这个问题。", elapsed_ms=50, max_wait_ms=800, max_chars=200)


def test_flushes_on_newline_boundary():
    assert should_flush_text("第一段\n", elapsed_ms=50, max_wait_ms=800, max_chars=200)


def test_flushes_on_wait_threshold():
    assert should_flush_text("半句话", elapsed_ms=801, max_wait_ms=800, max_chars=200)


def test_does_not_flush_tiny_fragment_too_early():
    assert not should_flush_text("半句话", elapsed_ms=100, max_wait_ms=800, max_chars=200)
