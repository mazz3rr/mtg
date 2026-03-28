"""

    mtg.deck.scrapers.pauperwave
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape Pauperwave decklists.

    @author: mazz3rr

"""
import contextlib
import logging
from typing import override

from bs4 import Tag

from mtg.constants import Json
from mtg.deck.abc import TagBasedDeckParser
from mtg.deck.scrapers.abc import HybridContainerScraper
from mtg.lib.scrape.core import ScrapingError, is_more_than_root_path, \
    parse_non_english_month_date, strip_url_query

_log = logging.getLogger(__name__)


class PauperwaveDeckTagParser(TagBasedDeckParser):
    """Parser of Pauperwave decklist HTML tag.
    """
    @override
    def _parse_metadata(self) -> None:
        title_tag = self._deck_tag.previous_sibling
        name, author, place = None, None, None
        if " by " in title_tag.text:
            name, author = title_tag.text.split(" by ", maxsplit=1)
            if ", " in author:
                author, place = author.split(", ", maxsplit=1)
        else:
            name = title_tag.text
        self._metadata["name"] = name
        if author:
            self._metadata["author"] = author
        if place:
            self._metadata.setdefault("event", {})["place"] = place

    @override
    def _parse_deck(self) -> None:
        qty = None
        for tag in self._deck_tag.descendants:
            if tag.name == "span":
                if "Commander" in tag.text:
                    self._state.shift_to_commander()
                elif "Sideboard" in tag.text:
                    self._state.shift_to_sideboard()
                elif "Companion" in tag.text:
                    self._state.shift_to_companion()
                else:
                    if not self._state.is_maindeck:
                        self._state.shift_to_maindeck()
            elif tag.name == "a" and tag.attrs["class"] == ["deckbox_link"]:
                name = tag.text.strip()
                playset = self.get_playset(self.find_card(name), qty)
                if self._state.is_maindeck:
                    self._maindeck += playset
                elif self._state.is_sideboard:
                    self._sideboard += playset
                elif self._state.is_commander:
                    for card in playset:
                        self._set_commander(card)
                elif self._state.is_companion:
                    self._companion = playset[0]
            elif tag.text.strip().isdigit():
                qty = int(tag.text.strip())

        self._metadata["format"] = "paupercommander" if self._commander else "pauper"


@HybridContainerScraper.registered
class PauperwaveArticleScraper(HybridContainerScraper):
    """Scraper of Pauperwave article page.
    """
    CONTAINER_NAME = "Pauperwave article"  # override
    TAG_BASED_DECK_PARSER = PauperwaveDeckTagParser  # override
    _MONTHS = [
        "Gennaio",
        "Febbraio",
        "Marzo",
        "Aprile",
        "Maggio",
        "Giugno",
        "Luglio",
        "Agosto",
        "Settembre",
        "Ottobre",
        "Novembre",
        "Dicembre",
    ]

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return is_more_than_root_path(url, "pauperwave.com")

    @staticmethod
    @override
    def sanitize_url(url: str) -> str:
        return strip_url_query(url)

    @override
    def _parse_metadata(self) -> None:
        if event_tag := self._soup.find("p", class_="has-medium-font-size"):
            self._metadata["event"] = {}
            seen, key = set(), ""
            for el in event_tag.descendants:
                if el.name == "br":
                    continue
                if el.text in seen:
                    continue
                seen.add(el.text)
                if el.text.endswith(":"):
                    key = el.text.lower().removesuffix(":")
                else:
                    match key:
                        case "players":
                            self._metadata["event"][key] = int(el.text)
                        case "date":
                            with contextlib.suppress(ValueError):
                                self._metadata["event"][key] = parse_non_english_month_date(
                                    el.text, *self._MONTHS)
                        case _:
                            self._metadata["event"][key] = el.text

    @override
    def _collect(self) -> tuple[list[str], list[Tag], list[Json], list[str]]:
        deck_tags = [*self._soup.find_all(
            "table", class_=lambda c: c and "mtg_deck" in c and "mtg_deck_embedded" in c)]
        article_tag = self._soup.find("article")
        if not article_tag:
            err = ScrapingError("Article tag not found", scraper=type(self), url=self.url)
            _log.warning(f"Scraping failed with: {err!r}")
            return [], deck_tags, [], []
        deck_urls, container_urls = self._find_links_in_tags(*article_tag.find_all("p"))
        return deck_urls, deck_tags, [], container_urls
