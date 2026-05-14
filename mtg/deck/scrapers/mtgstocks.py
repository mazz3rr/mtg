"""

    mtg.deck.scrapers.mtgstocks
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape MTGStocks decklists.

    @author: mazz3rr

"""
import logging
from datetime import UTC, datetime
from typing import override

import dateutil.parser
from bs4 import BeautifulSoup

from mtg.constants import Json, SECRETS
from mtg.deck.abc import DeckJsonParser
from mtg.deck.scrapers.abc import DeckScraper, DecksJsonContainerScraper
from mtg.lib.scrape.core import (
    ScrapingError, fetch_json, get_path_segments, strip_url_query,
)
from mtg.scryfall import Card

_log = logging.getLogger(__name__)
HEADERS = {
    "Host": "api.mtgstocks.com",
    "User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:150.0) Gecko/20100101 Firefox/150.0",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Referer": "https://www.mtgstocks.com/",
    "Origin": "https://www.mtgstocks.com",
    "Sec-GPC": "1",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
    "Connection": "keep-alive",
    "Cookie": SECRETS["mtgstocks"]["cookie"],
}


class MtgStocksDeckJsonParser(DeckJsonParser):
    """Parser of MTGStocks decklist JSON data.
    """
    @override
    def _parse_input_for_metadata(self) -> None:
        self._metadata["name"] = self._deck_json["name"]
        if date := self._deck_json.get("date") or self._deck_json.get("lastUpdated"):
            self._metadata["date"] = dateutil.parser.parse(date).date()
        if player := self._deck_json.get("player"):
            self._metadata["author"] = player
        self._update_fmt(self._deck_json["format"]["name"])
        if archetype := self._deck_json.get("archetype"):
            self._update_archetype_or_theme(archetype["name"])

    def _parse_card_json(self, card: Json) -> list[Card]:
        qty = int(card["quantity"])
        name = card["card"]["name"]
        return self.get_playset(self.find_card(name), qty)

    @override
    def _parse_input_for_decklist(self) -> None:
        # MTGStocks features only decks in non-commander formats:
        # legacy, vintage, modern, pioneer, standard, premodern and pauper
        for card in self._deck_json["boards"]["mainboard"]["cards"]:
            self._maindeck.extend(self._parse_card_json(card))
        if sideboard := self._deck_json["boards"].get("sideboard"):
            for card in sideboard["cards"]:
                self._sideboard.extend(self._parse_card_json(card))


def get_deck_json(deck_id: str) -> Json:
    api_url = f"https://api.mtgstocks.com/decks/{deck_id}"
    return fetch_json(api_url, headers=HEADERS)


@DeckScraper.registered
class MtgStocksDeckScraper(DeckScraper):
    """Scraper of MTGStocks decklist page.
    """
    JSON_FROM_API = True  # override
    EXAMPLE_URLS = (
        "https://www.mtgstocks.com/decks/481330",
    )

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

    def _get_deck_id(self) -> str:
        _, deck_id, *_ = get_path_segments(self.url)
        return deck_id

    @override
    def _fetch_json(self) -> None:
        deck_id = self._get_deck_id()
        self._json = get_deck_json(deck_id)

    @override
    def _validate_json(self) -> None:
        super()._validate_json()
        if not self._json.get("boards"):
            raise ScrapingError("No cards data", scraper=type(self), url=self.url)

    @override
    def _get_sub_parser(self) -> MtgStocksDeckJsonParser:
        return MtgStocksDeckJsonParser(self._json, self._metadata)

    @override
    def _parse_input_for_metadata(self) -> None:
        pass

    @override
    def _parse_input_for_decklist(self) -> None:
        pass


@DecksJsonContainerScraper.registered
class MtgStocksArticleScraper(DecksJsonContainerScraper):
    """Scraper of MTGStocks article page.
    """
    CONTAINER_NAME = "MTGStocks article"  # override
    JSON_FROM_API = True  # override
    DECK_JSON_PARSER_TYPE = MtgStocksDeckJsonParser  # override
    EXAMPLE_URLS = (
        "https://www.mtgstocks.com/news/22808-weekly-winners-2026---19",
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

    def _parse_article_id(self) -> str:
        _, slug, *_ = get_path_segments(self.url)
        article_id, *_ = slug.split("-")
        return article_id

    @override
    def _fetch_json(self) -> None:
        api_url = f"https://api.mtgstocks.com/news/{self._parse_article_id()}"
        self._json = fetch_json(api_url, headers=HEADERS)

    @override
    def _validate_json(self) -> None:
        super()._validate_json()
        if not self._json.get("content"):
            raise ScrapingError("No article content", scraper=type(self), url=self.url)

    @override
    def _parse_input_for_metadata(self) -> None:
        if author := self._json.get("user"):
            author = author["name"]
            self._metadata["author"] = author
            self._metadata.setdefault("article", {})["author"] = author
        if title := self._json.get("title"):
            self._metadata.setdefault("article", {})["title"] = title
        if dt := self._json.get("date"):
            self._metadata.setdefault("article", {})["date"] = datetime.fromtimestamp(
                dt / 1000, UTC).date()
        if cat := self._json.get("category"):
            self._metadata.setdefault("article", {})["category"] = cat["name"]

    @override
    def _parse_input_for_decks_data(self) -> None:
        soup = BeautifulSoup(self._json["content"], "lxml")
        deck_ids = [tag["[deckid]"] for tag in soup.select("news-deck")]
        self._decks_json = [get_deck_json(deck_id) for deck_id in deck_ids]
