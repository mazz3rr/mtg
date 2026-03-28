"""

    mtg.deck.abc
    ~~~~~~~~~~~~
    Abstract deck parsers.

    @author: mazz3rr

"""
from abc import abstractmethod
from typing import Self, override

from bs4 import Tag

from mtg.constants import Json
from mtg.deck.arena import ArenaParser
from mtg.deck.core import Deck, DeckParser


class NestedDeckParser(DeckParser):
    """Abstract deck parser with a sub-paser embedded within itself.

    This classic Decorator pattern resembling approach enables using an alternative parsing
    strategy in the sub-parser (e.g. text decklist based) - or, assuming arbitrary levels of
    nesting, an arbitrary number of such strategies can be employed.
    """
    def __init__(self, metadata: Json | None = None) -> None:
        super().__init__(metadata)
        self._decklist: str | None = None
        self._sub_parser: Self | None = None  # delegate parsing if needed

    @abstractmethod
    @override
    def _pre_parse(self) -> None:
        raise NotImplementedError

    @abstractmethod
    @override
    def _parse_metadata(self) -> None:
        raise NotImplementedError

    @abstractmethod
    @override
    def _parse_deck(self) -> None:
        raise NotImplementedError

    def _get_sub_parser(self) -> Self | None:
        """Return a sub-parser object to delegate deck-parsing to.

        By default, return an Arena/MGTO text decklist parser if the decklist is available.
        """
        if self._decklist:
            return ArenaParser(self._decklist, self._metadata)
        return None

    @override
    def _build_deck(self) -> Deck | None:
        self._sub_parser = self._get_sub_parser()
        if self._sub_parser:
            return self._sub_parser.parse()  # delegate
        return super()._build_deck()


class TagBasedDeckParser(NestedDeckParser):
    """Abstract HTML tag based deck parser.

    HTML tag based parsers process a single, decklist and metadata holding, HTML tag extracted
    from a webpage and return a Deck object (if able).
    """
    def __init__(self, deck_tag: Tag,  metadata: Json | None = None) -> None:
        super().__init__(metadata)
        self._deck_tag = deck_tag

    @override
    def _pre_parse(self) -> None:
        pass  # not utilized

    @abstractmethod
    @override
    def _parse_metadata(self) -> None:
        raise NotImplementedError

    @abstractmethod
    @override
    def _parse_deck(self) -> None:
        raise NotImplementedError


class JsonBasedDeckParser(NestedDeckParser):
    """Abstract JSON data based deck parser.

    JSON data based parsers process a single, decklist and metadata holding, piece of JSON data
    either dissected from a webpage's JavaScript code or obtained via a separate JSON API
    request and return a Deck object (if able).
    """
    def __init__(self,  deck_data: Json, metadata: Json | None = None) -> None:
        super().__init__(metadata)
        self._deck_data = deck_data

    @override
    def _pre_parse(self) -> None:
        pass  # not utilized

    @abstractmethod
    @override
    def _parse_metadata(self) -> None:
        raise NotImplementedError

    @abstractmethod
    @override
    def _parse_deck(self) -> None:
        raise NotImplementedError
