"""

    mtg.deck.scrapers.manastack
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape ManaStack decklists.

    @author: mazz3rr

"""
import logging
from typing import override

import dateutil.parser

from mtg.deck.scrapers.abc import DeckScraper, DeckUrlsContainerScraper
from mtg.lib.scrape.core import ScrapingError, fetch_json, get_path_segments, strip_url_query

_log = logging.getLogger(__name__)


@DeckScraper.registered
class ManaStackDeckScraper(DeckScraper):
    """Scraper of ManaStack decklist page.
    """
    JSON_FROM_API = True  # override
    URL_TEMPLATE = "https://manastack.com/deck/{slug}"
    EXAMPLE_URLS = (
        "https://manastack.com/deck/esper-transcendent-4",
        "https://manastack.com/deck/dustin-and-max-learned-tap-dancing",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return "manastack.com/deck/" in url.lower()

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = super().normalize_url(url)
        return strip_url_query(url)

    def _get_slug(self) -> str:
        _, slug, *_ = get_path_segments(self.url)
        return slug

    @override
    def _fetch_json(self) -> None:
        slug = self._get_slug()
        api_url = f"https://manastack.com/api/deck?slug={slug}"
        self._json = fetch_json(api_url)

    @override
    def _validate_json(self) -> None:
        super()._validate_json()
        if not self._json.get("cards"):
            raise ScrapingError("No cards data", scraper=type(self), url=self.url)

    @override
    def _parse_input_for_metadata(self) -> None:
        self._metadata["name"] = self._json["name"]
        self._metadata["author"] = self._json["owner"]["username"]
        self._update_fmt(self._json["format"]["name"])
        self._metadata["date"] = dateutil.parser.parse(self._json["last_updated"]["date"]).date()
        if desc := self._json.get("description"):
            self._metadata["description"] = desc
        comments = self._json.get("commentCount")
        if comments is not None:
            self._metadata["comments"] = comments
        if folder := self._json.get("folder"):
            self._metadata["folder"] = folder["name"]

    @override
    def _parse_input_for_decklist(self) -> None:
        for card_data in self._json["cards"]:
            sub_data = card_data["card"]
            collectors_number = sub_data["num"]
            set_code = sub_data["set"]["slug"]
            name = sub_data["name"]
            card = self.find_card(name, (set_code, collectors_number))
            if card_data["commander"]:
                self._set_commander(card)
            elif card_data["sideboard"]:
                self._sideboard.append(card)
            else:
                self._maindeck.append(card)


@DeckUrlsContainerScraper.registered
class ManaStackUserScraper(DeckUrlsContainerScraper):
    """Scraper of ManaStack user page.
    """
    CONTAINER_NAME = "ManaStack user"  # override
    DECK_SCRAPER_TYPES = ManaStackDeckScraper,  # override
    _SAMPLE_SIZE = 100  # this seems like enough
    EXAMPLE_URLS = (
        "https://manastack.com/user/kxdx1157/decks",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return "manastack.com/user/" in url.lower()

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = super().normalize_url(url)
        return strip_url_query(url)

    def _get_username(self) -> str:
        _, username, *_ = get_path_segments(self.url)
        return username

    @override
    def _parse_input_for_decks_data(self) -> None:
        api_url = f"https://manastack.com/api/decks/user/{self._get_username()}"
        data = fetch_json(api_url)
        if not data or not data.get("results"):
            raise ScrapingError("User data not found", scraper=type(self), url=self.url)
        results = data["results"][:self._SAMPLE_SIZE]
        self._deck_urls = [
            ManaStackDeckScraper.URL_TEMPLATE.format(**result) for result in results
        ]
