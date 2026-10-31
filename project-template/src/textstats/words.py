"""Count words in plain text. The tests in tests/test_words.py show the expected results."""

import re
from collections import Counter

WORD_PATTERN = re.compile(r"[a-z0-9']+")
"""A word is a run of letters, digits or apostrophes, after lowercasing."""


def split_words(text: str) -> list[str]:
    return WORD_PATTERN.findall(text.lower())


def top_words(text: str, count: int) -> list[tuple[str, int]]:
    """The ``count`` most common words, most common first. Ties keep first-seen order."""
    if count < 1:
        raise ValueError(f"count must be 1 or more, got {count}")
    return Counter(split_words(text)).most_common(count)
