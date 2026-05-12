"""

    mtg.deck.scrapers.manabox
    ~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape ManaBox decklists.

    @author: mazz3rr

"""
import contextlib
import json
import logging
from datetime import UTC, datetime
from typing import override

from mtg.deck.scrapers.abc import DeckScraper
from mtg.lib.scrape.core import ScrapingError, normalize_url

_log = logging.getLogger(__name__)


@DeckScraper.registered
class ManaBoxDeckScraper(DeckScraper):
    """Scraper of ManaBox decklist page.
    """
    JSON_FROM_SOUP = True  # override
    EXAMPLE_URLS = (
        "https://manabox.app/decks/rx5CcxGfTJqBx7mQSqVb4A",
        "https://manabox.app/decks/c_Qy5ZBeTra_gHuDV3xqzA",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return "manabox.app/decks/" in url.lower()

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        return normalize_url(url, case_sensitive=True)

    @override
    def _extract_json(self) -> None:
        data_tag = self._soup.find("astro-island", {"component-export": "Main"})
        if not data_tag:
            raise ScrapingError("No data tag found", scraper=type(self), url=self.url)
        _, self._json = json.loads(data_tag["props"])["deck"]

    @override
    def _validate_json(self) -> None:
        super()._validate_json()
        if not self._json.get("cards"):
            raise ScrapingError("No cards data", scraper=type(self), url=self.url)

    @override
    def _parse_input_for_metadata(self) -> None:
        _, self._metadata["name"] = self._json["name"]
        with contextlib.suppress(ValueError):
            _, fmt = self._json["format"]
            self._update_fmt(fmt)
        _, dt = self._json["editDate"]
        self._metadata["date"] = datetime.fromtimestamp(dt / 1000, UTC).date()

    @override
    def _parse_input_for_decklist(self) -> None:
        _, cards = self._json["cards"]
        for _, card_data in cards:
            _, set_code = card_data["setId"]
            _, collector_number = card_data["collectorNumber"]
            _, name = card_data["name"]
            _, qty = card_data["quantity"]
            card = self.find_card(name, (set_code, collector_number))
            playset = self.get_playset(card, qty)
            _, cat = card_data["boardCategory"]
            if cat == 0:
                for c in playset:
                    self._set_commander(c)
            elif cat == 3:
                self._maindeck += playset
            elif cat == 4:
                self._sideboard += playset
