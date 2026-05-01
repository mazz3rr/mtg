"""

    mtg.deck.scrapers.tcgrocks
    ~~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape TCGRocks decklists.

    TCGRocks is a sister site of MTGRocks and its decklists are sometimes featured in MTGRocks
    articles.

    @author: mazz3rr

"""
import contextlib
import json
import logging
from typing import override

import dateutil.parser

from mtg.constants import Json
from mtg.deck.scrapers.abc import DeckScraper
from mtg.lib.json import Node
from mtg.lib.scrape.core import ScrapingError, strip_url_query

_log = logging.getLogger(__name__)


@DeckScraper.registered
class TcgRocksDeckScraper(DeckScraper):
    """Scraper of TCGRocks decklist page.
    """
    JSON_FROM_SOUP = True  # override

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return "tcgrocks.com/mtg/deck-builder/embed/" in url.lower()

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = super().normalize_url(url)
        return strip_url_query(url)

    def _validate_soup(self) -> None:
        super()._validate_soup()
        script_tag = self._soup.select_one("script#__NUXT_DATA__")
        if not script_tag:
            raise ScrapingError(
                "Deck data <script> tag not found", scraper=type(self), url=self.url)

    def _get_json_from_soup(self) -> Json:
        script_tag = self._soup.select_one("script#__NUXT_DATA__")
        return json.loads(script_tag.text.strip())

    def _validate_json(self) -> None:
        super()._validate_json()
        if not isinstance(self._json, list) or "mtg" not in self._json:
            raise ScrapingError("No deck data", scraper=type(self), url=self.url)

    @override
    def _parse_input_for_metadata(self) -> None:
        if title_tag := self._soup.select_one("h2.text-center"):
            self._metadata["name"] = title_tag.text.strip()

        root = Node(self._json)
        for text in [n.data for n in root.find_all(lambda n: isinstance(n.data, str))]:
            with contextlib.suppress(dateutil.parser.ParserError):
                self._metadata["date"] = dateutil.parser.parse(text).date()
                break

        if keywords := self._metadata.get("article", {}).get("tags"):
            if fmt := self.derive_format_from_words(*keywords):
                self._update_fmt(fmt)

    @override
    def _parse_input_for_decklist(self) -> None:
        root = Node(self._json)
        mtg_node = root.find(lambda n: n.data == "mtg")
        decklist_node = mtg_node.next_sibling
        if decklist_node is None or not decklist_node.data or not isinstance(
                decklist_node.data, str):
            raise ScrapingError("Decklist not found", scraper=type(self), url=self.url)
        self._decklist = decklist_node.data
