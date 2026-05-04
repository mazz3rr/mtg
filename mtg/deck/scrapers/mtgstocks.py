"""

    mtg.deck.scrapers.mtgstocks
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape MTGStocks decklists.

    @author: mazz3rr

"""
import json
import logging
from typing import override

import dateutil.parser

from mtg.constants import Json
from mtg.deck.scrapers.abc import DeckScraper, DeckUrlsContainerScraper
from mtg.scryfall import Card
from mtg.lib.common import from_iterable
from mtg.lib.scrape.core import (
    ScrapingError, find_links, get_path_segments, prepend_url,
    strip_url_query,
)

_log = logging.getLogger(__name__)
URL_PREFIX = "https://mtgstocks.com"


@DeckScraper.registered
class MtgStocksDeckScraper(DeckScraper):
    """Scraper of MTGStocks decklist page.
    """
    JSON_FROM_SOUP = True  # override
    EXAMPLE_URLS = (
        "https://www.mtgstocks.com/decks/481330",
    )

    def __init__(self, url: str, metadata: Json | None = None) -> None:
        super().__init__(url, metadata)
        self._deck_id = self._parse_deck_id()

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        if "mtgstocks.com/decks/" not in url.lower():
            return False
        try:
            _, deck_id = get_path_segments(url)
            int(deck_id)
            return True
        except ValueError:
            return False

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = super().normalize_url(url)
        return strip_url_query(url).replace("/visual/", "/")

    def _parse_deck_id(self) -> int:
        _, deck_id = get_path_segments(self.url)
        return int(deck_id)

    def _extract_json(self) -> None:
        script_tag = self._soup.find("script", id="ng-state")
        if not script_tag:
            raise ScrapingError("<script> not found", scraper=type(self), url=self.url)
        data = json.loads(script_tag.text)
        deck_data = from_iterable(
            [v for v in data.values() if isinstance(v, dict)],
            lambda v: v.get("b") and v["b"].get("id") and v["b"]["id"] == self._deck_id)
        if not deck_data:
            raise ScrapingError(
                "Deck data missing in <script> tag's JSON", scraper=type(self), url=self.url)
        self._json = deck_data["b"]

    @override
    def _parse_input_for_metadata(self) -> None:
        self._metadata["name"] = self._json["name"]
        if date := self._json.get("lastUpdated"):
            self._metadata["date"] = dateutil.parser.parse(date).date()
        if player := self._json.get("player"):
            self._metadata["author"] = player
        self._update_fmt(self._json["format"]["name"])

    def _parse_card_json(self, card: Json) -> list[Card]:
        qty = int(card["quantity"])
        name = card["card"]["name"]
        return self.get_playset(self.find_card(name), qty)

    @override
    def _parse_input_for_decklist(self) -> None:
        for card in self._json["boards"]["mainboard"]["cards"]:
            self._maindeck.extend(self._parse_card_json(card))
        if sideboard := self._json["boards"].get("sideboard"):
            for card in sideboard["cards"]:
                self._sideboard.extend(self._parse_card_json(card))


@DeckUrlsContainerScraper.registered
class MtgStocksArticleScraper(DeckUrlsContainerScraper):
    """Scraper of MTGStocks article page.
    """
    CONTAINER_NAME = "MTGStocks article"  # override
    EXAMPLE_URLS = (
        "https://www.mtgstocks.com/news/1061-weekly-winners-2021---20",
        "https://www.mtgstocks.com/news/15416-weekly-winners-2025---02",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return "mtgstocks.com/news/" in url.lower()

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = super().normalize_url(url)
        return strip_url_query(url)

    def _collect_other_urls(self) -> list[str]:
        article_tag = self._soup.find("article")
        if not article_tag:
            err = ScrapingError("Article tag not found", scraper=type(self), url=self.url)
            _log.warning(f"Scraping failed with: {err!r}")
            return []
        links = find_links(article_tag)
        return [l for l in links if any(ds.is_valid_url(l) for ds in self._get_deck_scraper_types())]

    def _collect_own_urls(self) -> list[str]:
        deck_tags = self._soup.find_all("news-deck")
        a_tags = [tag.find("a", href=lambda h: h and "/decks/" in h) for tag in deck_tags]
        return [prepend_url(t.attrs["href"], URL_PREFIX) for t in a_tags if t is not None]

    def _parse_input_for_decks_data(self) -> None:
        self._deck_urls = sorted({*self._collect_other_urls(), *self._collect_own_urls()})
