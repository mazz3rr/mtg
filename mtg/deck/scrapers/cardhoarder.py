"""

    mtg.deck.scrapers.cardhoarder
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape Cardhoarder decklists.

    @author: mazz3rr

"""
import json
import logging
from typing import override

import dateutil.parser

from mtg.constants import Json
from mtg.deck.scrapers.abc import DeckScraper
from mtg.lib.scrape.core import ScrapingError, dissect_js, strip_url_query
from mtg.yt.discover import UrlHook

_log = logging.getLogger(__name__)


URL_HOOKS = (
    # regular deck
    UrlHook(
        ('"cardhoarder.com/d/"', ),
    ),
)


# Cardhoarder has anti-scraping protection (I doubt they care much about user-posted decks
# though), but it seems it's bypassed by Selenium
@DeckScraper.registered
class CardhoarderDeckScraper(DeckScraper):
    """Scraper of Cardhoarder decklist page.
    """
    SELENIUM_PARAMS = {  # override
        "xpath": "//div[contains(@id, 'deck-viewer')]"
    }
    JSON_FROM_SOUP = True  # override
    EXAMPLE_URLS = (
        "https://www.cardhoarder.com/d/kpy674jn6ekerj5",
    )

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return "cardhoarder.com/d/" in url.lower()

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = super().normalize_url(url)
        return strip_url_query(url)

    @override
    def _get_json_from_soup(self) -> Json:
        start = 'const props = JSON.parse('
        end = ');\n\t\t\twindow.Cardhoarder.helpers.addDeckViewer('
        # in this case, it returns raw JSON string instead of dict...
        deck_data = dissect_js(self._soup, start, end)
        if not deck_data:
            raise ScrapingError(
                "Nothing extracted from JavaScript", scraper=type(self), url=self.url)
        # ...that needs to be reparsed
        return json.loads(deck_data)

    @override
    def _parse_input_for_metadata(self) -> None:
        self._metadata["name"] = self._json["deck"]["name"]
        self._metadata["date"] = dateutil.parser.parse(self._json["deck"]["updated_at"]).date()

    @override
    def _parse_input_for_decklist(self) -> None:
        maindeck, sideboard = [], []
        for item in self._json["items"]:
            card_data = item["card"]["card_data"]
            name = card_data["name"]
            set_code = card_data["scryfall_set_code"]
            collector_number = card_data["collector_number"]
            scryfall_id = card_data["scryfall_id"]
            card = self.find_card(name, (set_code, collector_number), scryfall_id)

            quantity_main = int(item["quantity_main"])
            quantity_sideboard = int(item.get("quantity_sideboard", 0))
            maindeck += self.get_playset(card, quantity_main)
            if quantity_sideboard:
                sideboard += self.get_playset(card, quantity_sideboard)

        if len(maindeck) in (1, 2):
            for card in maindeck:
                self._set_commander(card)
            self._maindeck = sideboard
        else:
            self._maindeck = maindeck
            self._sideboard = sideboard
