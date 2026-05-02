"""

    mtg.deck.scrapers.pauperwave
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape Pauperwave decklists.

    @author: mazz3rr

"""
import contextlib
import logging
from typing import override

from mtg.deck.abc import DeckTagParser
from mtg.deck.scrapers.abc import HybridContainerScraper
from mtg.lib.scrape.core import (
    ScrapingError, is_more_than_root_path,
    parse_non_english_month_date, strip_url_query,
)

_log = logging.getLogger(__name__)


class PauperwaveDeckTagParser(DeckTagParser):
    """Parser of Pauperwave decklist HTML tag.
    """
    @override
    def _parse_input_for_metadata(self) -> None:
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
    def _parse_input_for_decklist(self) -> None:
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
    DECK_TAG_PARSER_TYPE = PauperwaveDeckTagParser  # override
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
    EXAMPLE_URLS = (
        "https://blog.pauperwave.org/articles/2026-04-26-pauperancino",  # decklist
        "https://blog.pauperwave.org/articles/2026-03-31-dennis-garbati-paupergeddon-spring-2026", # report
        "https://blog.pauperwave.org/articles/2025-12-06-tutorial-pingers",  # tutorial
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return is_more_than_root_path(url, "blog.pauperwave.org/articles/")

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = super().normalize_url(url)
        return strip_url_query(url)

    # TODO: as a result of the site's overhaul the below no longer work (#470)
    @override
    def _parse_input_for_metadata(self) -> None:
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
    def _parse_input_for_decks_data(self) -> None:
        self._deck_tags = [*self._soup.find_all(
            "table", class_=lambda c: c and "mtg_deck" in c and "mtg_deck_embedded" in c)]
        article_tag = self._soup.find("article")
        if not article_tag:
            err = ScrapingError("Article tag not found", scraper=type(self), url=self.url)
            _log.warning(f"Scraping failed with: {err!r}")
            return
        self._deck_urls, self._container_urls = self._find_links_in_tags(*article_tag.find_all("p"))
