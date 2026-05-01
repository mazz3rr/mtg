"""

    mtg.deck.scrapers.mtgotraders
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape MTGO Traders decklists.

    The site is in a state of permanent shutdown sins December 2023. But, decklist pages still
    seem operational.

    @author: mazz3rr

"""
import logging
from datetime import datetime
from typing import override

from mtg.constants import Json
from mtg.deck.scrapers.abc import DeckScraper
from mtg.lib.scrape.core import fetch_json
from mtg.scryfall import Card

_log = logging.getLogger(__name__)


# @DeckScraper.registered
class MtgoTradersDeckScraper(DeckScraper):
    """Scraper of MTGO Traders deck page.
    """
    API_URL_TEMPLATE = "https://www.mtgotraders.com/deck/data/getdeck.php?deck={}"  # override
    EXAMPLE_URLS = (
        # doesn't work - service to retire
        "https://www.mtgotraders.com/deck/#/view?deck=3118",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return "www.mtgotraders.com/deck/" in url.lower() and "?deck=" in url.lower()

    @override
    def _get_json_from_api(self) -> Json:
        *_, decklist_id = self.url.split("?deck=")
        return fetch_json(self.API_URL_TEMPLATE.format(decklist_id))

    @override
    def _parse_input_for_metadata(self) -> None:
        self._metadata["name"] = self._json["Name"]
        if desc :=self._json.get("Description"):
            self._metadata["description"] = desc
        self._update_fmt(self._json["Format"])
        self._metadata["author"] = self._json["User"]
        self._metadata["date"] = datetime.strptime(
            self._json["Date"], "%Y-%m-%d %H:%M:%S").date()
        self._metadata["views"] = self._json["ViewCount"]
        self._metadata["downloads"] = self._json["DownloadCount"]
        self._metadata["ratings"] = self._json["TotalRatings"]
        self._metadata["avg_rating"] = self._json["RatingAvg"]

    def _parse_json_card(self, json_card: Json) -> list[Card]:
        name = json_card["name"]
        quantity = json_card["qty"]
        return self.get_playset(self.find_card(name), quantity)

    @override
    def _parse_input_for_decklist(self) -> None:
        for json_card in self._json["main"]:
            self._maindeck += self._parse_json_card(json_card)
        if sideboard := self._json.get("sideboard"):
            for json_card in sideboard:
                self._sideboard += self._parse_json_card(json_card)
        self._derive_commander_from_sideboard()
