"""

    mtg.deck.scrapers.scryfall
    ~~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape Scryfall decklists.

    @author: mazz3rr

"""
import logging
from datetime import date
from typing import override

from bs4 import Tag

from mtg.deck.scrapers.abc import DeckScraper
from mtg.lib.text import sanitize_whitespace
from mtg.lib.numbers import extract_int
from mtg.lib.scrape.core import get_netloc_domain, get_path_segments, strip_url_query
from mtg.scryfall import Card

_log = logging.getLogger(__name__)


@DeckScraper.registered
class ScryfallDeckScraper(DeckScraper):
    """Scraper of Scryfall decklist page.
    """
    EXAMPLE_URLS = (
        "https://scryfall.com/@MTGBudgetBrew/decks/4f54de61-96ac-475c-8071-215a82b72975?as=list&with=usd",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        url = url.lower()
        user_segment, segment, *_ = get_path_segments(url)
        domain = get_netloc_domain(url, naked=True)
        return (
            domain == "scryfall.com"
            and user_segment.startswith("@")
            and segment == "decks"
        )

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = super().normalize_url(url)
        url = strip_url_query(url)
        return f"{url}?as=list&with=usd"

    @override
    def _parse_input_for_metadata(self) -> None:
        author_part, *_ = get_path_segments(self.url)
        self._metadata["author"] = author_part.removeprefix("@")
        if name_tag := self._soup.find("h1", class_="deck-details-title"):
            self._metadata["name"] = sanitize_whitespace(name_tag.text.strip())
        info_tag = self._soup.find("p", class_="deck-details-subtitle")
        if fmt_tag := info_tag.find("strong"):
            self._update_fmt(sanitize_whitespace(fmt_tag.text.strip()))
        date_text = info_tag.find("abbr").attrs["title"]
        date_text, _ = date_text.split(" ", maxsplit=1)
        self._metadata["date"] = date.fromisoformat(date_text)
        if desc_tag := self._soup.find("div", class_="deck-details-description"):
            self._metadata["description"] = sanitize_whitespace(desc_tag.text.strip())

    @classmethod
    def _parse_section_tag(cls, section_tag: Tag) -> list[Card]:
        cards = []
        for li_tag in section_tag.find_all("li"):
            quantity = extract_int(li_tag.find("span", class_="deck-list-entry-count").text)
            name_tag = li_tag.find("span", class_="deck-list-entry-name")
            name = name_tag.text.strip()
            if name.endswith("✶"):
                name = name.removesuffix("✶").strip()
            link = name_tag.find("a").attrs["href"]
            text = link.removeprefix("https://scryfall.com/card/")
            set_code, collector_number, *_ = text.split("/")
            card = cls.find_card(name, (set_code, collector_number))
            cards += cls.get_playset(card, quantity)
        return cards

    @override
    def _parse_input_for_decklist(self) -> None:
        for section_tag in self._soup.find_all("div", class_="deck-list-section"):
            title = section_tag.find("h6").text
            cards = self._parse_section_tag(section_tag)

            if "Commander" in title:
                for card in cards:
                    self._set_commander(card)
            elif "Sideboard" in title:
                self._sideboard = cards
            else:
                self._maindeck += cards
