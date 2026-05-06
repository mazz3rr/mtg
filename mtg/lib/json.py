"""

    mtg.lib.json
    ~~~~~~~~~~~~~~
    Utilities for JSON handling.

    @author: mazz3rr

"""
import contextlib
import json
import re
from collections import OrderedDict
from collections.abc import Callable, Iterator
from datetime import date, datetime
from typing import Any, Literal, Self

import njsparser

from mtg.constants import Json, READABLE_TIMESTAMP_FORMAT


def serialize_dates(obj: Any) -> str:
    """Custom serializer for dates.

    To be used with json.dump() as ``default`` parameter.
    """
    if isinstance(obj, datetime):
        return obj.strftime(READABLE_TIMESTAMP_FORMAT)
    elif isinstance(obj, date):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")


def deserialize_dates(dct: dict) -> dict:
    """Custom deserializer for dates.

    To be used with json.load() as ``object_hook`` parameter.
    """
    for key, value in dct.items():
        if isinstance(value, str):
            # try to parse as datetime
            try:
                dct[key] = datetime.strptime(value, READABLE_TIMESTAMP_FORMAT)
            except ValueError:
                # if it fails, try to parse as date
                with contextlib.suppress(ValueError):
                    # leave it as a string if both parsing attempts fail
                    dct[key] = date.fromisoformat(value)
    return dct


def recursive_sort(obj: Json) -> Json:
    """Recursively sort any dicts contained in ``obj`` (by their keys).
    """
    if isinstance(obj, dict):
        return OrderedDict(
            (k, recursive_sort(v))
            for k, v in sorted(obj.items())
        )
    elif isinstance(obj, list):
        return [recursive_sort(x) for x in obj]
    return obj


def to_json(data: Json, sort_dictionaries=False, indent: int | None = 4) -> str:
    data = recursive_sort(data) if sort_dictionaries else data
    return json.dumps(data, indent=indent, ensure_ascii=False, default=serialize_dates)


def from_json(json_text: str) -> Json:
    return json.loads(json_text, object_hook=deserialize_dates)


class Node:
    """A recursive tree-building wrapper on dict/list deserialized from JSON.
    """
    @property
    def ancestors(self) -> tuple[Self, ...]:
        return self._ancestors

    @property
    def parent(self) -> Self | None:
        if not self.ancestors:
            return None
        return self.ancestors[0]

    @property
    def key(self) -> str | int:
        return self._key

    @property
    def data(self) -> Json:
        return self._data

    @property
    def children(self) -> tuple[Self, ...]:
        return self._children

    @property
    def name(self) -> str:
        if isinstance(self.key, str):
            name = f"[{self.key!r}]"
        elif isinstance(self.key, int):
            name = f"[{self.key}]"
        else:
            name = "/"
        return name

    @property
    def path(self) -> str:
        return self.build_path(*self.ancestors[::-1], self)

    @property
    def is_leaf(self) -> bool:
        return not self.children

    @property
    def is_root(self) -> bool:
        return not self.ancestors

    @property
    def is_str(self) -> bool:
        return isinstance(self.data, str)

    @property
    def is_list(self) -> bool:
        return isinstance(self.data, list)

    @property
    def is_dict(self) -> bool:
        return isinstance(self.data, dict)

    @property
    def next_sibling(self) -> Self | None:
        if self.is_root:
            return None
        try:
            return self.parent.children[self.parent.children.index(self) + 1]
        except IndexError:
            return None

    @property
    def previous_sibling(self) -> Self | None:
        if self.is_root:
            return None
        try:
            return self.parent.children[self.parent.children.index(self) - 1]
        except IndexError:
            return None

    def __init__(self, data: Json, *ancestors: Self, key: str | int | None = None) -> None:
        self._data, self._ancestors, self._key = data, ancestors, key
        self._children = self._get_children()

    def _get_children(self) -> tuple[Self, ...]:
        if isinstance(self.data, dict):
            return tuple(Node(d, self, *self.ancestors, key=k) for k, d in self.data.items())
        if isinstance(self.data, list):
            return tuple(Node(d, self, *self.ancestors, key=i) for i, d in enumerate(self.data))
        return ()

    def __len__(self) -> int:
        return len(self.children)

    def __str__(self) -> str:
        return self.name

    def __bool__(self) -> bool:
        return True

    # NOTE: nodes are unique and hashable as long as only their 'path' is considered
    def __eq__(self, other: Self) -> bool:
        if not isinstance(other, Node):
            return NotImplemented
        return self.path == other.path

    def __hash__(self) -> int:
        return hash(self.path)

    def __iter__(self) -> Iterator[Self]:
        return iter(self.children)

    def iter(self) -> Iterator[Self]:
        """Yield all descendants of this node.
        """
        for descendant in self.find_all():
            yield descendant

    @classmethod
    def build_path(cls, *nodes: Self) -> str:
        return "".join(str(node) for node in nodes)

    # recursive
    def find_all(
            self,
            predicate: Callable[[Self], bool] = lambda _: True) -> Iterator[Self]:
        """Traverse the tree from this node downwards and yield all nodes that satisfy
        ``predicate`` along the way. If no predicate is provided, all sub-nodes are yielded.

        Note that this node in not included in the traversal.

        Args:
            predicate: function that evaluates the traversed tree node to a boolean value

        Returns:
            a generator of all tree nodes that satisfy the predicate
        """
        for child in self.children:
            if predicate(child):
                yield child
        for child in self.children:
            yield from child.find_all(predicate)

    def find(self, predicate: Callable[[Self], bool]) -> Self | None:
        """Find the first node within this node's subtree that satisfies ``predicate`` or None.

        Args:
            predicate: function that evaluates the traversed tree node to a boolean value

        Returns:
            tree node or None if it cannot be found
        """
        return next(self.find_all(predicate), None)

    def find_all_by_path(
            self, path: str,
            mode: Literal["exact", "start", "end", "partial", "regex"] = "exact") -> Iterator[Self]:
        """Find all nodes within this node's subtree by their structural path.

        Modes:
            * "exact": path is matched, if it is equal to the node's path
            * "start": path is matched, if it starts the node's path
            * "end": path is matched, if it ends the node's path
            * "partial": path is matched, if it is a substring of the node's path
            * "regex": path is matched as a regular expression against the node's path

        Args:
            path: structural path to test, e.g.: "['descriptionBodyText']['runs'][0]['text']"
            mode: how to match the path

        Returns:
            a generator of all matched tree nodes
        """
        match mode:
            case "exact":
                return self.find_all(lambda n: n.path == path)
            case "start":
                return self.find_all(lambda n: n.path.startswith(path))
            case "end":
                return self.find_all(lambda n: n.path.endswith(path))
            case "partial":
                return self.find_all(lambda n: path in n.path)
            case "regex":
                try:
                    pattern = re.compile(path)
                except re.error as exc:
                    raise ValueError(f"Invalid regex pattern: {path!r}") from exc
                return self.find_all(lambda n: bool(pattern.search(n.path)))
            case _:
                raise ValueError(f"Unrecognized mode {mode!r}")

    def find_by_path(
            self, path: str,
            mode: Literal["exact", "start", "end", "partial", "regex"] = "exact"
        ) -> Self | None:
        """Find the first node within this node's subtree by its structural path.

        Modes:
            * "exact": path is matched, if it is equal to the node's path
            * "start": path is matched, if it starts the node's path
            * "end": path is matched, if it ends the node's path
            * "partial": path is matched, if it is a substring of the node's path
            * "regex": path is matched as a regular expression against the node's path

        Args:
            path: structural path to test, e.g.: "['descriptionBodyText']['runs'][0]['text']"
            mode: how to match the path

        Returns:
            tree node or None if it cannot be found
        """
        return next(self.find_all_by_path(path, mode), None)

    def text_nodes(self) -> Iterator[Self]:
        return self.find_all(lambda n: n.is_str)


def node_from_njs_fd_markup(markup: str) -> Node:
    """Return a Node wrapper on JSON data extracted from passed HTML markup by njsparser.

    This function assumes the passed markup contains a valid Next.js flight data. Otherwise,
    all hell can break loose: https://chatgpt.com/s/t_69fba6b23b6c81918681c2eb3a2e7f47
    """
    fd = njsparser.BeautifulFD(markup)
    parsed_json = json.dumps(fd, default=njsparser.default)
    parsed_data = json.loads(parsed_json)
    return Node(parsed_data)

