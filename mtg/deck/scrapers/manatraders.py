"""

    mtg.deck.scrapers.manatraders
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape Manatraders decklists.

    @author: mazz3rr

"""
import json
import logging
from typing import override

from mtg.constants import Json
from mtg.deck.scrapers.abc import DeckScraper, DeckUrlsContainerScraper
from mtg.lib.scrape.core import ScrapingError, prepend_url, strip_url_query

_log = logging.getLogger(__name__)
URL_PREFIX = "https://www.manatraders.com"


@DeckScraper.registered
class ManatradersDeckScraper(DeckScraper):
    """Scraper of Manatraders decklist page.
    """
    JSON_FROM_SOUP = True  # override
    EXAMPLE_URLS = (
        "https://www.manatraders.com/webshop/personal/806941?medium=Nikachu",
        "https://www.manatraders.com/webshop/deck/6712289",
    )

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        if "manatraders.com/webshop/personal/" in url.lower():
            return True
        if "manatraders.com/webshop/deck/" in url.lower():
            return True
        return False

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = super().normalize_url(url)
        return strip_url_query(url)

    @override
    def _get_json_from_soup(self) -> Json:
        data_tag = self._soup.find("div", {"data-react-class": "WebshopApp"})
        if not data_tag:
            data_tag = self._soup.find("div", {"data-react-class": "DeckBuilder"})
            if not data_tag:
                raise ScrapingError("Deck tag not found", scraper=type(self), url=self.url)
        json_data = json.loads(data_tag.attrs["data-react-props"])
        if deck_json := json_data.get("deck"):
            return deck_json
        if deck_json := json_data.get("initialDeck"):
            return deck_json
        raise ScrapingError("Deck data missing in extracted JSON", scraper=type(self), url=self.url)

    @override
    def _parse_input_for_metadata(self) -> None:
        self._metadata["name"] = self._json["name"]
        if author := self._json.get("playerName"):
            self._metadata["author"] = author
        self._update_fmt(self._json["format"])
        self._update_archetype_or_theme(self._json["archetype"])

    def _parse_card_json(self, card_json: Json) -> None:
        name = card_json["name"]
        card = self.find_card(name)
        if quantity := card_json.get("quantity"):
            self._maindeck += self.get_playset(card, quantity)
        if sideboard_qty := card_json.get("sideboardQuantity"):
            self._sideboard += self.get_playset(card, sideboard_qty)

    @override
    def _parse_input_for_decklist(self) -> None:
        for card_data in self._json["cards"].values():
            self._parse_card_json(card_data)
        self._derive_commander_from_sideboard()


@DeckUrlsContainerScraper.registered
class ManatradersUserScraper(DeckUrlsContainerScraper):
    """Scraper of Manatraders user search page.
    """
    CONTAINER_NAME = "Manatraders user"  # override
    DECK_SCRAPER_TYPES = ManatradersDeckScraper,  # override
    EXAMPLE_URLS = (
        "https://www.manatraders.com/decks?format_id=4&search_name=kasa",
    )

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return all(t in url.lower() for t in ("manatraders.com/decks?", "search_name"))

    @override
    def _parse_input_for_decks_data(self) -> None:
        deck_tags = [
            tag for tag in self._soup.find_all("a", href=lambda h: h and "/webshop/deck/" in h)]
        if not deck_tags:
            raise ScrapingError("Deck tags not found", scraper=type(self), url=self.url)
        urls = {tag.attrs["href"] for tag in deck_tags}
        self._deck_urls = [strip_url_query(prepend_url(url, URL_PREFIX)) for url in sorted(urls)]
