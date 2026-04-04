"""

    mtg.lib.text
    ~~~~~~~~~~~~
    Text-related utilities.

    @author: mazz3rr

"""
import ast
import hashlib
import re
from typing import Any, Type

from lingua import Language, LanguageDetectorBuilder


def getrepr(class_: Type, *name_value_pairs: tuple[str, Any]) -> str:
    """Return ``__repr__`` string format: 'ClassName(name=value, ..., name_n=value_n)'

    Args:
        class_: class to get repr for
        name_value_pairs: variable number of (name, value) tuples
    """
    reprs = [f"{name}={value!r}" for name, value in name_value_pairs]
    return f"{class_.__name__}({', '.join(reprs)})"


def camel_case_split(text: str) -> list[str]:
    """Do camel-case split on ``text``.

    Taken from:
        https://stackoverflow.com/a/58996565/4465708

    Args:
        text: text to be split

    Returns:
        a list of parts
    """
    bools = [char.isupper() for char in text]
    # mark change of case
    upper_chars_indices = [0]  # e.g.: [0, 8, 8, 17, 17, 25, 25, 28, 29]
    for (i, (first_char_is_upper, second_char_is_upper)) in enumerate(zip(bools, bools[1:])):
        if first_char_is_upper and not second_char_is_upper:  # "Cc"
            upper_chars_indices.append(i)
        elif not first_char_is_upper and second_char_is_upper:  # "cC"
            upper_chars_indices.append(i + 1)
    upper_chars_indices.append(len(text))
    # for "cCc", index of "C" will pop twice, have to filter that
    return [text[x:y] for x, y in zip(upper_chars_indices, upper_chars_indices[1:]) if x < y]


def sanitize_whitespace(text: str) -> str:
    """Replace whitespace sequences longer than one space in ``text`` with a single space.
    Replace non-breaking space with a regular one.
    """
    text = text.replace(' ', " ")
    return re.sub(r'\s+', ' ', text)


def remove_furigana(text: str) -> str:
    """Remove parenthesized furigana (content within Japanese parentheses `（）`) from a string.
    Return the cleaned string with only the base kanji and other characters.

    Args:
        text: input string, e.g., "嵐（あらし）の討（とう）伐（ばつ）者（しゃ）、エルズペス"

    Returns:
        str: cleaned string, e.g., "嵐の討伐者、エルズペス"
    """
    # pattern: matches `（` followed by any characters (non-greedy) until `）`
    return re.sub(r'（.*?）', '', text)


# based on: https://x.com/i/grok/share/KQ8Luq4TiwRq93XHXY1IUmsfg
def decode_escapes(text: str) -> str:
    """Decode text with doubly escaped sequences, e.g. with `\\n` instead of `\n` or `\\'`
    instead of `'`.

    This is common in online data scenarios when JSON is serialized from a source (e.g a database)
    that already includes escaped characters (e.g. `\n` for newlines or `\'` for single quotes).
    The JSON parser interprets the outer layer of escaping, leaving single backslashes in the
    resulting string, which then need further processing to handle the inner escapes like `\\n` or
    `\\'`.
    """
    escape_map = {
        r'\\n': '\n',
        r"\\'": "'",
        r'\\"': '"',
        r'\\\\': '\\'  # handle literal backslashes
    }
    try:
        return ast.literal_eval(f'"{text}"')
    except (SyntaxError, ValueError):
        # fall back to targeted escape sequence replacement
        for escaped, actual in escape_map.items():
            text = text.replace(escaped, actual)
        return text


def get_hash(text: str, truncation=0, sep="", legacy=False) -> str:
    """Return SHA-256 hash of ``text``.

    Args:
        text: text to hash
        truncation: number of characters to truncate to (default: no truncation)
        sep: character separator (after each 8th character - for increased readability (default: no separator))
        legacy: do it the old way for legacy reasons
    """
    truncation = 0 if truncation < 0 else truncation
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()

    if legacy:  # TODO: to be removed when #50 is finished
        h = sha[:32]
        res = []
        for i, ch in enumerate(h):
            if i == 8 or i == 12 or i == 16 or i == 20:
                res.append("-")
            res.append(ch)
        return "".join(res)

    if truncation:
        sha = sha[:truncation]
    if sep:
        res = []
        for i, ch in enumerate(sha):
            if i > 0 and i % 8 == 0:
                res.append(sep)
            res.append(ch)
        sha = "".join(res)
    return sha


# list of languages Magic: The Gathering cards have been printed in
MTG_LANGS = {
    Language.ENGLISH,
    Language.FRENCH,
    Language.GERMAN,
    Language.ITALIAN,
    Language.SPANISH,
    Language.JAPANESE,
    Language.PORTUGUESE,
    Language.CHINESE,
    Language.RUSSIAN,
    Language.KOREAN,
}


def detect_mtg_lang(text: str) -> Language:
    """Detect language of ``text`` checking against those that Magic: The Gathering cards have
    been printed in.

    Args:
        text: MtG card text to detect the language of

    Raises:
        ValueError: if the detected language is not a Magic: The Gathering card language

    Returns:
        lingua.Language object
    """
    detector = LanguageDetectorBuilder.from_languages(*MTG_LANGS).build()
    detected_lang = detector.detect_language_of(text)
    if not detected_lang:
        raise ValueError("No language detected")
    if detected_lang in MTG_LANGS:
        return detected_lang
    raise ValueError(
        f"Detected language {detected_lang.name} is not a Magic: The Gathering card language")


def is_foreign(text: str) -> bool:
    try:
        lang = detect_mtg_lang(text)
    except ValueError:
        return False
    if lang.iso_code_639_1.name.lower() != "en":
        return True
    return False
