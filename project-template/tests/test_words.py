import pytest

from textstats.words import split_words, top_words


def test_split_words_lowercases_and_drops_punctuation():
    assert split_words("Hello, hello! It's 2 o'clock.") == ["hello", "hello", "it's", "2", "o'clock"]


def test_top_words_orders_by_count():
    assert top_words("b a b c b a", 2) == [("b", 3), ("a", 2)]


def test_top_words_empty_text_gives_empty_list():
    assert top_words("", 3) == []


def test_top_words_rejects_count_below_one():
    """Regression test: count=0 returned [] silently. Now it is an error."""
    with pytest.raises(ValueError):
        top_words("a", 0)
