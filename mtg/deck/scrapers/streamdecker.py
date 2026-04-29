"""

    mtg.deck.scrapers.streamdecker
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape Streamdecker decklists.

    @author: mazz3rr

"""
import logging
from datetime import date
from typing import override

from requests import ReadTimeout

from mtg.constants import Json
from mtg.deck.scrapers.abc import DeckScraper, DeckUrlsContainerScraper
from mtg.lib.time import get_date_from_ago_text
from mtg.lib.scrape.core import ScrapingError, fetch_json, strip_url_query

_log = logging.getLogger(__name__)


@DeckScraper.registered
class StreamdeckerDeckScraper(DeckScraper):
    """Scraper of Streamdecker deck page.
    """
    API_URL_TEMPLATE = "https://www.streamdecker.com/api/deck/{}"  # override
    EXAMPLE_URLS = (
        "https://www.streamdecker.com/deck/ESysr7yWC",
    )

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return "www.streamdecker.com/deck/" in url.lower()

    @staticmethod
    @override
    def normalize_url(url: str) -> str:
        return strip_url_query(url)

    @override
    def _get_json_from_api(self) -> Json:
        *_, decklist_id = self.url.split("/")
        try:
            json_data = fetch_json(self.API_URL_TEMPLATE.format(decklist_id))
        except ReadTimeout as rt:
            raise ScrapingError("API request timed out", scraper=type(self), url=self.url) from rt
        if not json_data or not json_data.get("data") or json_data["data"] == {"deck": {}}:
            raise ScrapingError("No deck data", scraper=type(self), url=self.url)
        return json_data["data"]

    def _parse_date(self) -> date | None:
        date_text = self._json["updatedAt"]
        return get_date_from_ago_text(date_text)

    @override
    def _parse_input_for_metadata(self) -> None:
        self._metadata.update({
            "name": self._json["name"],
            "views": self._json["views"]["counter"]
        })
        self._metadata["author"] = self._json["userProfile"]["displayName"]
        self._metadata["author_twitch_id"] = self._json["userProfile"]["twitchId"]
        if dt := self._parse_date():
            self._metadata["date"] = dt

    def _parse_json_card(self, json_card: Json) -> None:
        scryfall_id = json_card.get("scryfallId", "")
        name = json_card["name"]
        card = self.find_card(name, scryfall_id=scryfall_id)
        if json_card["main"]:
            self._maindeck.extend(self.get_playset(card, json_card["main"]))
        if json_card["sideboard"]:
            self._sideboard.extend(self.get_playset(card, json_card["sideboard"]))
        if json_card.get("commander"):
            cards = self.get_playset(card, json_card["commander"])
            for card in cards:
                self._set_commander(card)
        if json_card.get("companion"):
            self._companion = card

    @override
    def _parse_input_for_decklist(self) -> None:
        for json_card in self._json["cardList"]:
            self._parse_json_card(json_card)


@DeckUrlsContainerScraper.registered
class StreamdeckerUserScraper(DeckUrlsContainerScraper):
    """Scraper of Streamdecker user page.
    """
    CONTAINER_NAME = "Streamdecker user"  # override
    API_URL_TEMPLATE = "https://www.streamdecker.com/api/userdecks/{}"  # override
    DECK_SCRAPER_TYPES = StreamdeckerDeckScraper,  # override
    DECK_URL_PREFIX = "https://www.streamdecker.com/deck/"  # override
    EXAMPLE_URLS = (
        "https://www.streamdecker.com/decks/togoresmtg",
    )

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return "streamdecker.com/decks/" in url.lower()

    @staticmethod
    @override
    def normalize_url(url: str) -> str:
        return strip_url_query(url)

    @override
    def _get_json_from_api(self) -> Json:
        *_, user_name = self.url.split("/")
        return fetch_json(self.API_URL_TEMPLATE.format(user_name))

    @override
    def _validate_json(self) -> None:
        super()._validate_json()
        if not self._json.get("data") or not self._json["data"].get("decks"):
            raise ScrapingError("No decks data", scraper=type(self), url=self.url)

    @override
    def _parse_input_for_decks_data(self) -> None:
        self._deck_urls = [d["deckLink"] for d in self._json["data"]["decks"]]
