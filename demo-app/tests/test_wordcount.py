from textkit import char_count, word_count


def test_word_count():
    assert word_count("one two three") == 3
    assert word_count("  spaced   out  ") == 2
    assert word_count("") == 0


def test_char_count():
    assert char_count("abc") == 3
    assert char_count("a b c") == 5
    assert char_count("a b c", include_spaces=False) == 3
