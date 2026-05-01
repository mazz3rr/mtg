"""

    mtg.deck.scrapers.mtgarenapro
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape defunct MTGArena.Pro decklists (using Wayback Machine).

    The tracker app is dead and the website was sold by the developer at some time:
    https://www.reddit.com/r/MTGArenaPro/comments/1l9e9zb/is_mtgarena_pro_dead/

    @author: mazz3rr

"""
import logging
from typing import override

import dateutil.parser

from mtg.constants import Json
from mtg.deck.scrapers.abc import DeckScraper
from mtg.lib.scrape.core import ScrapingError, dissect_js
from mtg.scryfall import Card

_log = logging.getLogger(__name__)
ALT_DOMAIN = "mtga.cc"


# @DeckScraper.registered
class MtgArenaProDeckScraper(DeckScraper):
    """Scraper of MTGArena.Pro decklist page.
    """
    USE_WAYBACK = True  # override
    JSON_FROM_SOUP = True  # override
    EXAMPLE_URLS = (
        # doesn't work - service to retire
        "https://mtgarena.pro/decks/doomed-snapper-pauper-1/",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return "mtgarena.pro/decks/" in url.lower() or f"{ALT_DOMAIN}/decks/" in url.lower()

    @staticmethod
    @override
    def normalize_url(url: str) -> str:
        return url.replace(ALT_DOMAIN, "mtgarena.pro")

    @override
    def _get_json_from_soup(self) -> Json:
        return dissect_js(
            self._soup, "var precachedDeck=", '"card_ids":', lambda s: s + '"card_ids":[]}')

    @override
    def _validate_json(self) -> None:
        super()._validate_json()
        if not self._json.get("deck_order"):
            raise ScrapingError("No 'deck_order' data", scraper=type(self), url=self.url)

    def _parse_fmt(self) -> str:
        if self._json["explorer"]:
            return "explorer"
        elif self._json["timeless"]:
            return "timeless"
        elif self._json["alchemy"]:
            return "alchemy"
        elif self._json["brawl"]:
            if self._json["standard"]:
                return "standardbrawl"
            return "brawl"
        elif self._json["historic"]:
            return "historic"
        elif self._json["standard"]:
            return "standard"
        return ""

    @override
    def _parse_input_for_metadata(self) -> None:
        self._metadata["author"] = self._json["author"]
        self._metadata["name"] = self._json["humanname"]
        if fmt := self._parse_fmt():
            self._update_fmt(fmt)
        self._metadata["date"] = dateutil.parser.parse(self._json["date"]).date()

    @classmethod
    def _parse_card_json(cls, card_json: Json) -> list[Card]:
        name = card_json["name"]
        quantity = card_json["cardnum"]
        card = cls.find_card(name)
        return cls.get_playset(card, quantity)

    @override
    def _parse_input_for_decklist(self) -> None:
        for card_json in self._json["deck_order"]:
            self._maindeck.extend(self._parse_card_json(card_json))
        for card_json in self._json["sidedeck_order"]:
            self._sideboard.extend(self._parse_card_json(card_json))
        for card_json in self._json["commander_order"]:
            self._set_commander(self._parse_card_json(card_json)[0])
