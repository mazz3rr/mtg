"""

    mtg.deck.scrapers.manabox
    ~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape ManaBox decklists.

    @author: mazz3rr

"""
import logging
from typing import override

import dateutil.parser
from bs4 import Tag

from mtg.deck.scrapers.abc import DeckScraper
from mtg.lib.scrape.core import normalize_url
from mtg.scryfall import Card

_log = logging.getLogger(__name__)


@DeckScraper.registered
class ManaBoxDeckScraper(DeckScraper):
    """Scraper of ManaBox decklist page.
    """
    EXAMPLE_URLS = (
        "https://manabox.app/decks/rx5CcxGfTJqBx7mQSqVb4A",
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
    def _parse_input_for_metadata(self) -> None:
        info_tag = self._soup.find("div", class_="w-full").find("div", class_="mb-2")
        name_tag, _, fmt_tag, date_tag, *_ = info_tag.find_all("div")
        self._metadata["name"] = name_tag.text.strip()
        fmt, *_ = fmt_tag.text.strip().split(" - ")
        self._update_fmt(fmt)
        self._metadata["date"] = dateutil.parser.parse(date_tag.text.strip()).date()

    @classmethod
    def _parse_container_div(cls, container_div: Tag) -> list[Card]:
        cards = []
        for card_tag in container_div.find_all("div", class_=["hidden", "md:block"]):
            qty_tag, name_tag = card_tag.find_all("div", class_=lambda c: c and "text-sm" in c)
            qty, name = int(qty_tag.text.strip()), name_tag.text.strip()
            cards += cls.get_playset(cls.find_card(name), qty)
        return cards

    @override
    def _parse_input_for_decklist(self) -> None:
        for container_div in self._soup.find_all("div", class_="mb-3"):
            header_tag = container_div.find(
                "div", class_=["flex", "whitespace-nowrap", "overflow-hidden", "text-ellipsis"])
            if "Commander" in header_tag.text:
                for card in self._parse_container_div(container_div):
                    self._set_commander(card)
            elif "Sideboard" in header_tag.text:
                self._sideboard += self._parse_container_div(container_div)
            else:
                self._maindeck += self._parse_container_div(container_div)
