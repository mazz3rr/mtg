"""

    mtg.deck.scrapers.edhtop16
    ~~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape EDHTop16 decklist containers.

    @author: mazz3rr

"""
import contextlib
import logging
from typing import override

import dateutil.parser

from mtg.constants import Json
from mtg.deck.arena import ArenaParser
from mtg.deck.core import Deck
from mtg.deck.scrapers.abc import DeckUrlsContainerScraper
from mtg.deck.scrapers.topdeck import check_unexpected_urls
from mtg.lib.scrape.core import ScrapingError, dissect_js, strip_url_query
from mtg.yt.discover import UrlHook

_log = logging.getLogger(__name__)
URL_HOOKS = (
    # tournament
    UrlHook(
        ('"edhtop16.com/tournament/"', ),
    ),
    # commander
    UrlHook(
        ('"edhtop16.com/commander/"', ),
    ),
)


@DeckUrlsContainerScraper.registered
class EdhTop16TournamentScraper(DeckUrlsContainerScraper):
    """Scraper of EDHTop16 tournament page.
    """
    CONTAINER_NAME = "EDHTop16 tournament"  # override
    JSON_FROM_SOUP = True  # override
    EXAMPLE_URLS = (
        "https://edhtop16.com/tournament/Mh2edH1jY19LaTovso33",
        "https://edhtop16.com/tournament/landfall-3er-clasificatorio-al-nacional-de-cedh",
    )

    def __init__(self, url: str, metadata: Json | None = None) -> None:
        super().__init__(url, metadata)
        self._arena_decklists = []

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return "edhtop16.com/tournament/" in url.lower()

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = super().normalize_url(url)
        return strip_url_query(url)

    def _process_json(self, json_data: Json) -> Json:
        match json_data:
            case [[_, {"tournament": data}]]:
                return data
            case _:
                raise ScrapingError("No tournament data", scraper=type(self), url=self.url)

    @override
    def _get_json_from_soup(self) -> Json:
        end_processor = lambda s: s.replace('":undefined', '":null').replace(
                '": undefined', '": null').rstrip(";")
        start_hook = "window.__router_ops = "
        json_data = dissect_js(self._soup, start_hook, end_processor=end_processor)
        if json_data is None:
            raise ScrapingError(
                "Data <script> tag not found", scraper=type(self), url=self.url)
        if not json_data:
            raise ScrapingError("No decks data", scraper=type(self), url=self.url)
        return self._process_json(json_data)

    @override
    def _validate_json(self) -> None:
        super()._validate_json()
        if "entries" not in self._json:
            raise ScrapingError("No tournament entries data", scraper=type(self), url=self.url)

    @override
    def _parse_input_for_metadata(self) -> None:
        self._update_fmt("commander")
        self._metadata["event"] = {}
        self._metadata["event"]["name"] = self._json.get("name")
        if size := self._json.get("size"):
            self._metadata["event"]["size"] = size
        if date := self._json.get("tournamentDate"):
            self._metadata["event"]["date"] = dateutil.parser.parse(date).date()

    @staticmethod
    def _normalize_decklist(decklist: str) -> str:
        tokens = decklist.split("1 ")[1:]
        commander, *playsets = [f"1 {t}" for t in tokens if t]
        return "\n".join(["Commander", commander, "", "Deck"] + [*playsets])

    def _process_decklist(self, decklist: str | None, urls: list[str]) -> None:
        if decklist:
            decklist = decklist.strip()
            if decklist.lower().startswith("http"):
                urls.append(decklist)
            else:
                with contextlib.suppress(ValueError):
                    self._arena_decklists.append(self._normalize_decklist(decklist))

    @override
    def _parse_input_for_decks_data(self) -> None:
        deck_urls = []
        for entry in self._json["entries"]:
            self._process_decklist(entry.get("decklist", ""), deck_urls)
        check_unexpected_urls(deck_urls, *self._get_deck_scraper_types())
        self._deck_urls = deck_urls

    @override
    def scrape_decks(self) -> list[Deck]:
        decks = super().scrape_decks()
        if self._arena_decklists:
            _log.info(
                f"Gathered {len(self._arena_decklists)} text decklists from a {self.CONTAINER_NAME}"
                f" at: {self.url!r}")
            for arena_decklist in self._arena_decklists:
                if deck := ArenaParser(arena_decklist, metadata=self._metadata).parse():
                    decks.append(deck)
        return decks


@DeckUrlsContainerScraper.registered
class EdhTop16CommanderScraper(EdhTop16TournamentScraper):
    """Scraper of EDHTop16 commander page.
    """
    CONTAINER_NAME = "EDHTop16 commander"  # override
    EXAMPLE_URLS = (
        "https://edhtop16.com/commander/Niv-Mizzet%2C%20Visionary",
    )

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return "edhtop16.com/commander/" in url.lower()

    @override
    def _process_json(self, json_data: Json) -> Json:
        match json_data:
            case [[_, {"commander": data}]]:
                return data
            case _:
                raise ScrapingError("No commander data", scraper=type(self), url=self.url)

    @override
    def _validate_json(self) -> None:
        if not self._json or "entries" not in self._json or not self._json["entries"].get("edges"):
            raise ScrapingError("No commander entries data", scraper=type(self), url=self.url)

    @override
    def _parse_input_for_metadata(self) -> None:
        self._update_fmt("commander")
        self._metadata["commander"] = {}
        self._metadata["commander"]["name"] = self._json["name"]
        self._metadata["commander"]["entries"] = self._json["stats"]["count"]
        self._metadata["commander"]["meta_share"] = self._json["stats"]["metaShare"]
        self._metadata["commander"]["conversion_rate"] = self._json["stats"]["conversionRate"]

    @override
    def _parse_input_for_decks_data(self) -> None:
        deck_urls = []
        for edge in self._json["entries"]["edges"]:
            decklist = edge["node"].get("decklist", "")
            self._process_decklist(decklist, deck_urls)
        check_unexpected_urls(deck_urls, *self._get_deck_scrapers())
        self._deck_urls = deck_urls
