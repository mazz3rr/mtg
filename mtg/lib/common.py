"""

    mtg.lib.common
    ~~~~~~~~~~~~~~
    Common utilities.

    @author: mazz3rr

"""
import contextlib
import itertools
import logging
from collections import Counter
from typing import Callable, Iterable, Protocol, Sequence, Type

_log = logging.getLogger(__name__)


class ParsingError(ValueError):
    """Raised on unexpected states of parsed data.
    """


def from_iterable[T](iterable: Iterable[T], predicate: Callable[[T], bool]) -> T | None:
    """Return item from ``iterable`` based on ``predicate`` or ``None``, if it cannot be found.
    """
    return next((item for item in iterable if predicate(item)), None)


def totuple(lst: list) -> tuple:  # recursive
    """Convert ``lst`` and any list it contains (no matter the nesting level) recursively to tuple.

    Taken from:
        https://stackoverflow.com/a/27050037/4465708
    """
    return tuple(totuple(i) if isinstance(i, list) else i for i in lst)


def tolist(tpl: tuple) -> list:  # recursive
    """Convert ``tpl`` and any tuple it contains (no matter the nesting level) recursively to list.

    Taken from and made in reverse:
        https://stackoverflow.com/a/27050037/4465708
    """
    return list(tolist(i) if isinstance(i, tuple) else i for i in tpl)


def cleardir(obj: object) -> list[str]:
    """Return ``dir(obj)`` without extraneous fluff.
    """
    return [attr for attr in dir(obj) if not attr.startswith("_")]


def breadcrumbs(*crumbs: str) -> str:
    """Return a breadcrumb string based on ``crumbs`` supplied.

    Example:
        `/foo/bar/fiz/baz`
    """
    return "/" + "/".join(crumbs)


class MarkdownTableCounter(Counter):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._max_ord = len(str(len(self))) if self else 0
        self._max_name = max(len(name) for name in self) if self else 0
        self._max_count = len(str(max(count for count in self.values()))) if self else 0

    def print(self, title="") -> None:
        """Print this object in a neat table (with an optional title).
        """
        if not self:
            return

        if title:
            print(f" {title} ".center(
                self._max_ord + self._max_name + self._max_count + 16, "-"))
        for j, (name, count) in enumerate(self.most_common(), start=1):
            percent = f"{count * 100 / self.total():.2f} %"
            print(
                f"{j}.".ljust(self._max_ord + 1),
                name.ljust(self._max_name + 1),
                str(count).rjust(self._max_count + 1),
                f"({percent})".rjust(10),
            )
        print(
            f"".ljust(self._max_ord + 1),
            "TOTAL".ljust(self._max_name + 1),
            str(self.total()).rjust(self._max_count + 1),
            f"({100:.2f} %)".rjust(10),
        )

    def markdown(self, col_name="") -> str:
        """Turn this object into a Markdown table.

        Args:
            col_name: name of the main column
        """
        if not self:
            return ""

        markdown = []

        col_name = col_name or "Name"
        markdown.append(f"| No | {col_name} | Count | Percentage |")
        markdown.append("|:---|:-----|------:|-----------:|")

        total_count = self.total()

        for j, (name, count) in enumerate(self.most_common(), start=1):
            percent = f"{count * 100 / total_count:.2f} %"
            markdown.append(
                f"| {j:<{self._max_ord}} | {name:<{self._max_name}} "
                f"| {count:>{self._max_count}} | {percent:>10} |")

        markdown.append(
            f"|{'':<{self._max_ord}}| {'TOTAL':<{self._max_name}} "
            f"| {self.total():>{self._max_count}} | {100:.2f} %|")

        return "\n".join(markdown)


class Comparable(Protocol):
    """Protocol for annotating comparable types.
    """
    def __lt__(self, other) -> bool:
        ...


def is_increasing(seq: Sequence[Comparable]) -> bool:
    if len(seq) < 2:
        return False
    return all(seq[i] > seq[i-1] for i, _ in enumerate(seq, start=1) if i < len(seq))


def find_longest_seqs(seq: list[int]) -> list[list[int]]:
    """Return a list of the longest increasing sequences in ``seq``.
    """
    if not is_increasing(seq):
        raise ValueError("Sequence must be increasing.")
    seqs = []
    for _, g in itertools.groupby(enumerate(seq), lambda pair: pair[1] - pair[0]):
        current_seq = [pair[1] for pair in g]
        seqs.append(current_seq)

    max_len = max(len(s) for s in seqs)
    return [group for group in seqs if len(group) == max_len]


@contextlib.contextmanager
def logging_disabled(level: int = logging.CRITICAL):
    previous_level = logging.root.manager.disable
    logging.disable(level)
    try:
        yield
    finally:
        logging.disable(previous_level)


# Registry Pattern
def register_type(
        registry: set[Type], registered_type: Type, parent_type: Type | None = None) -> None:
    """Register a type in the supplied registry. If ``parent_type`` is defined,
    the registered type must be a subclass of it.
    """
    if parent_type and not issubclass(registered_type, parent_type):
        raise TypeError(f"Not a subclass of {parent_type.__name__}: {registered_type!r}")
    registry.add(registered_type)


def fullqualname(class_: Type) -> str:
    """Return fully qualified name of ``class_``.

    Example: 'builtins.int'
    """
    return f"{class_.__module__}.{class_.__name__}"


def types_to_namestr(types: Iterable[Type]) -> str:
    """Convert ``types`` to a string representation using their fully qualified names.

    Example: '[builtins.str, builtins.int, builtins.float]'
    """
    return ", ".join([fullqualname(t) for t in types])

