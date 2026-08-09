"""What language a report is written in, read off the request.

Nobody is asked. A user who types *«تحلیل فروش سه ماه اخیر»* has already said
which language they want their document in, and asking again is a question
whose answer is on screen. So the language is **derived**, the way a run's
status is derived from its sections: one source of truth, no second control to
contradict it.

Pure and token-free, like `checks.py` beside it — script, not vocabulary. A
model call to classify a string this feature already has in memory would be a
cost and a failure mode bought for nothing, and it would put a network round
trip in the path of creating a report.

The detection is a *count*, not a first-match: a Persian request naming Latin
table names ("درآمد از orders در سه ماه اخیر") is a Persian request, and an
English one quoting a Persian product name is an English one. Whichever script
carries more of the letters carries the document.

Only the languages the prose prompts actually name are returned — see
`LANGUAGE_NAMES` in `prompts.py`. Anything else is written in English, which
is the same fallback `LANGUAGE_NAMES.get` has always applied.
"""
from __future__ import annotations

# The Perso-Arabic ranges, as ranges rather than an alphabet: Persian, Arabic
# and Urdu all live here, and a report in any of them is closer to being right
# in Persian — right-to-left, with Eastern Arabic numerals — than in English.
_RTL_RANGES = (
    (0x0600, 0x06FF),  # Arabic
    (0x0750, 0x077F),  # Arabic Supplement
    (0x08A0, 0x08FF),  # Arabic Extended-A
    (0xFB50, 0xFDFF),  # Arabic Presentation Forms-A
    (0xFE70, 0xFEFF),  # Arabic Presentation Forms-B
)

DEFAULT_LANGUAGE = "en"


def _is_rtl(char: str) -> bool:
    point = ord(char)
    return any(low <= point <= high for low, high in _RTL_RANGES)


def detect(*candidates: str) -> str:
    """The language of the first candidate that has letters in it.

    Several candidates because the request is what a report is written from,
    but a user may leave it empty and name the report *«گزارش فروش»* — and a
    document titled in Persian and written in English is a bug the user did
    not ask for. They are tried in order rather than concatenated: joining a
    Latin name to a Persian request would let the name outvote the thing the
    whole document is written towards.
    """
    for text in candidates:
        rtl = sum(1 for char in text if _is_rtl(char))
        latin = sum(1 for char in text if char.isascii() and char.isalpha())
        if rtl == 0 and latin == 0:
            continue  # digits, punctuation, emoji: no evidence either way
        return "fa" if rtl > latin else "en"
    return DEFAULT_LANGUAGE
