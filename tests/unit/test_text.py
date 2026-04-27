from hermes_feishu_card.text import (
    StreamingTextNormalizer,
    normalize_stream_text,
    should_flush_text,
)


def test_normalize_removes_think_tags():
    raw = "<think>我在分析</think>\n最终不会出现标签"
    assert normalize_stream_text(raw) == "我在分析\n最终不会出现标签"


def test_normalize_removes_mixed_case_think_tags():
    raw = "<THINK>我在分析</Think>\n最终不会出现标签"
    assert normalize_stream_text(raw) == "我在分析\n最终不会出现标签"


def test_normalize_handles_empty_input():
    assert normalize_stream_text("") == ""
    assert normalize_stream_text(None) == ""


def test_streaming_normalizer_removes_split_think_tags():
    normalizer = StreamingTextNormalizer()

    chunks = ["<thi", "nk>分片</thi", "nk>"]
    result = "".join(normalizer.feed(chunk) for chunk in chunks)

    assert result == "分片"


def test_streaming_normalizer_removes_mixed_case_split_think_tags():
    normalizer = StreamingTextNormalizer()

    chunks = ["<TH", "INK>分片</Th", "ink>"]
    result = "".join(normalizer.feed(chunk) for chunk in chunks)

    assert result == "分片"


def test_flushes_on_chinese_sentence_end():
    assert should_flush_text("我先分析这个问题。", elapsed_ms=50, max_wait_ms=800, max_chars=200)


def test_flushes_on_newline_boundary():
    assert should_flush_text("第一段\n", elapsed_ms=50, max_wait_ms=800, max_chars=200)


def test_flushes_on_wait_threshold():
    assert should_flush_text("半句话", elapsed_ms=801, max_wait_ms=800, max_chars=200)


def test_flushes_on_equal_wait_threshold():
    assert should_flush_text("半句话", elapsed_ms=800, max_wait_ms=800, max_chars=200)


def test_flushes_on_equal_max_chars():
    assert should_flush_text("四个字", elapsed_ms=50, max_wait_ms=800, max_chars=3)


def test_force_flushes_empty_buffer():
    assert should_flush_text("", elapsed_ms=0, max_wait_ms=800, max_chars=200, force=True)


def test_does_not_flush_tiny_fragment_too_early():
    assert not should_flush_text("半句话", elapsed_ms=100, max_wait_ms=800, max_chars=200)
