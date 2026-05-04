"""

    mtg.deck.scrapers.seventeen
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape 17Lands decklists.

    @author: mazz3rr

"""
import logging
from typing import override

from requests import ReadTimeout

from mtg.constants import Json
from mtg.deck.scrapers.abc import DeckScraper
from mtg.lib.scrape.core import (
    ScrapingError, fetch_json, get_netloc_domain, get_path_segments,
    strip_url_query,
)
from mtg.scryfall import Card

_log = logging.getLogger(__name__)


@DeckScraper.registered
class SeventeenLandsDeckScraper(DeckScraper):
    """Scraper of 17Lands decklist page.
    """
    JSON_FROM_API = True  # override
    EXAMPLE_URLS = (
        "https://www.17lands.com/user/deck/eba7a011b7e84f8cb286492312cf4241/85624423/1734473634",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        url = url.lower()
        segments = get_path_segments(url)
        try:
            first, second, sharing_token, deck_id, timestamp = segments
        except ValueError:
            return False
        domain = get_netloc_domain(url, naked=True)
        return (
            domain == "17lands.com"
            and first == "user"
            and second == "deck"
        )

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = super().normalize_url(url)
        url = strip_url_query(url).removesuffix("/primer").removesuffix("/history")
        return url.rstrip(".,")

    @override
    def _fetch_json(self) -> None:
        _, _, sharing_token, deck_id, timestamp = get_path_segments(self.url)
        api_url = (
            f"https://www.17lands.com/data/user_deck?sharing_token={sharing_token}&deck="
            f"{deck_id}&timestamp={timestamp}"
        )
        try:
            self._json = fetch_json(api_url)
        except ReadTimeout:
            raise ScrapingError("API request timed out", scraper=type(self), url=self.url)

    @override
    def _validate_json(self) -> None:
        super()._validate_json()
        if not self._json.get("groups") or not self._json.get("cards"):
            raise ScrapingError("No deck data", scraper=type(self), url=self.url)

    @override
    def _parse_input_for_metadata(self) -> None:
        pass

    def _parse_card_json(self, card_json: Json) -> Card:
        name = card_json["name"]
        scryfall_id, _ = card_json["image_url"].split(".jpg?", maxsplit=1)
        scryfall_id = scryfall_id.removeprefix("https://cards.scryfall.io/large/front/")
        *_, scryfall_id = scryfall_id.split("/")
        return self.find_card(name, scryfall_id=scryfall_id)

    @override
    def _parse_input_for_decklist(self) -> None:
        maindeck_card_ids = self._json["groups"][0]["cards"]
        try:
            sideboard_card_ids = self._json["groups"][1]["cards"]
        except IndexError:
            sideboard_card_ids = []

        for card_data in self._json["cards"].values():
            card = self._parse_card_json(card_data)
            self._maindeck += [card] * maindeck_card_ids.count(card_data["id"])
            if sideboard_card_ids:
                self._sideboard += [card] * sideboard_card_ids.count(card_data["id"])
