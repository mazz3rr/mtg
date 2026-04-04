"""

    mtg.lib.numbers
    ~~~~~~~~~~~~~~~
    Numbers-related utilities.

    @author: mazz3rr

"""
import logging

from mtg.lib.common import ParsingError

_log = logging.getLogger(__name__)


def extract_float(text: str) -> float:
    """Extract floating point number from text.
    """

    num = "".join([char for char in text if char.isdigit() or char in ",."])
    if not num:
        raise ParsingError(f"No digits or decimal point in text: {text!r}")
    return float(num.replace(",", "."))


def extract_int(text: str) -> int:
    """Extract an integer from text.
    """
    num = "".join([char for char in text if char.isdigit()])
    if not num:
        raise ParsingError(f"No digits in text: {text!r}")
    return int(num)


def getfloat(string: str | None) -> float | None:
    """Interpret string as floating point number or, if not possible, return None.
    """
    if not string:
        return None
    try:
        return extract_float(string)
    except ValueError:
        return None


def getint(string: str | None) -> int | None:
    """Interpret string as integer or, if not possible, return None.
    """
    if not string:
        return None
    try:
        return extract_int(string)
    except ValueError:
        return None


def getbool(string: str | None) -> bool | None:
    """Interpret string as boolean value or, if not possible, return None
    """
    if not string:
        return None
    if string.lower() == "false":
        return False
    elif string.lower() == "true":
        return True
    return None


def multiply_by_symbol(number: float, symbol: str) -> int:
    """Multiply ``number`` by ``symbol`` and return it.
    """
    if symbol in ('K', 'k'):
        return int(number * 1_000)
    elif symbol in ('M', 'm'):
        return int(number * 1_000_000)
    elif symbol in ('B', 'b'):
        return int(number * 1_000_000_000)
    elif symbol in ('T', 't'):
        return int(number * 1_000_000_000_000)
    if symbol:
        _log.warning(f"Unsupported symbol for multiplication: {symbol!r}")
    return int(number)


def get_ordinal_suffix(num: int) -> str:
    """Return the ordinal suffix of ``num``.
    """
    return "th" if 11 <= num % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(num % 10, "th")
