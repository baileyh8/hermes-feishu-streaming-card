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


def test_normalize_removes_thinking_tags():
    assert normalize_stream_text("<thinking>推理中</thinking>结果") == "推理中结果"
    assert normalize_stream_text("<THINKING>推理</THINKING>") == "推理"


def test_streaming_normalizer_handles_thinking_split_across_chunks():
    normalizer = StreamingTextNormalizer()
    assert normalizer.feed("hello<think") == "hello"
    assert normalizer.feed("ing>world") == "world"


def test_normalize_does_not_remove_plain_think_word():
    assert normalize_stream_text("I think so") == "I think so"
    assert normalize_stream_text("I am thinking") == "I am thinking"


# —— 表格统计 ————————————————————————————————

def test_count_markdown_tables_zero():
    from hermes_feishu_card.text import count_markdown_tables
    assert count_markdown_tables("hello world") == 0
    assert count_markdown_tables("| name | age |") == 0  # no separator


def test_count_markdown_tables_normal():
    from hermes_feishu_card.text import count_markdown_tables
    text = "| a | b |\n| --- | --- |\n| 1 | 2 |\n\n| x | y |\n| --- | --- |\n| 3 | 4 |"
    assert count_markdown_tables(text) == 2


def test_count_markdown_tables_seven():
    from hermes_feishu_card.text import count_markdown_tables
    text = "\n\n".join([f"| col |\n| --- |\n| {i} |" for i in range(7)])
    assert count_markdown_tables(text) == 7


def test_max_card_tables_constant():
    from hermes_feishu_card.text import MAX_CARD_TABLES
    assert MAX_CARD_TABLES == 5


# —— Markdown-aware text splitting ————————————————————————————————


def test_split_markdown_blocks_short_text():
    from hermes_feishu_card.text import split_markdown_blocks
    assert split_markdown_blocks("你好世界", 2400) == ["你好世界"]


def test_split_markdown_blocks_empty():
    from hermes_feishu_card.text import split_markdown_blocks
    assert split_markdown_blocks("", 2400) == [""]


def test_split_markdown_blocks_at_limit():
    from hermes_feishu_card.text import split_markdown_blocks
    text = "X" * 2400
    assert split_markdown_blocks(text, 2400) == [text]


def test_split_markdown_blocks_at_paragraph_boundary():
    from hermes_feishu_card.text import split_markdown_blocks
    para_a = "A" * 1500
    para_b = "B" * 1500
    text = para_a + "\n\n" + para_b
    chunks = split_markdown_blocks(text, 2000)
    assert len(chunks) == 2
    assert len(chunks[0]) <= 2000
    assert len(chunks[1]) <= 2000
    assert "A" in chunks[0] and "B" in chunks[1]


def test_split_markdown_blocks_preserves_table():
    from hermes_feishu_card.text import split_markdown_blocks
    table = "| 功能 | 说明 |\n|------|------|\n| ASR | 识别中文 |\n| VAD | 静音切割 |"
    text = "A" * 1000 + "\n\n" + table + "\n\n" + "B" * 1000
    chunks = split_markdown_blocks(text, 1200)
    # The table should be kept intact within a single chunk
    table_chunks = [c for c in chunks if "ASR" in c and "VAD" in c]
    assert len(table_chunks) == 1
    assert "功能" in table_chunks[0]


def test_split_markdown_blocks_preserves_fenced_code_block():
    from hermes_feishu_card.text import split_markdown_blocks
    code = "```python\nprint('hello')\nprint('world')\n```"
    text = "X" * 1000 + "\n\n" + code + "\n\n" + "Y" * 1000
    chunks = split_markdown_blocks(text, 1100)
    code_chunks = [c for c in chunks if "```python" in c]
    assert len(code_chunks) == 1
    assert "```" in code_chunks[0]


def test_split_markdown_blocks_merges_small_paragraphs():
    from hermes_feishu_card.text import split_markdown_blocks
    # Multiple small paragraphs that fit in one chunk
    paras = "\n\n".join(["P" + str(i) * 100 for i in range(5)])
    chunks = split_markdown_blocks(paras, 2400)
    # All should fit in one chunk
    assert len(chunks) == 1
    for i in range(5):
        assert f"P{i}" in chunks[0]


def test_split_markdown_blocks_oversized_paragraph_without_breaks():
    from hermes_feishu_card.text import split_markdown_blocks
    # Multiple long lines without paragraph breaks — splits at line boundaries
    long_text = "Hello world " * 400 + "\n" + "Goodbye world " * 400 + "\n" + "Foo bar baz " * 400
    chunks = split_markdown_blocks(long_text, 2000)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(chunk) <= 2000
    assert all(c for c in chunks), "No empty chunks"
