"""

    mtg.deck.scrapers.cycles
    ~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape Cycles Gaming decklists.

    @author: mazz3rr

"""
import contextlib
import logging
import re
from typing import override

import dateutil.parser
from bs4 import NavigableString, Tag

from mtg.constants import Json
from mtg.deck.abc import DeckTagParser
from mtg.deck.scrapers.abc import HybridContainerScraper
from mtg.lib.scrape.core import is_more_than_root_path, strip_url_query
from mtg.yt.discover import UrlHook

_log = logging.getLogger(__name__)
URL_HOOKS = (
    # article
    UrlHook(
        ('"cyclesgaming.com/"', ),
    ),
)


class CyclesGamingDeckTagParser(DeckTagParser):
    """Parser of Cycles Gaming decklist HTML tag.
    """
    def __init__(self, deck_tag: Tag, metadata: Json | None = None) -> None:
        super().__init__(deck_tag, metadata)
        self._parsed_multifaced = set()

    @override
    def _parse_input_for_metadata(self) -> None:
        self._metadata["name"] = self._deck_tag.text.strip().removeprefix("Decklist – ")

    @staticmethod
    def _parse_text_for_quantity(text: str) -> int:
        # matches a number at the start OR a number inside (brackets) at the end
        match = re.search(r'^(\d+)| \((\d+)\)$', text)

        if match:
            # return whichever group (start or end) found the number
            count = match.group(1) or match.group(2)
            return int(count)

        return 1  # default to 1 if no quantity is specified

    def _parse_tag_for_playset(self, tag: Tag) -> None:
        name = tag.text.strip()
        previous = tag.previous_sibling.text if isinstance(
            tag.previous_sibling, NavigableString) else ""
        next_ = tag.next_sibling.text if isinstance(
            tag.next_sibling, NavigableString) else ""
        qty_text = previous + tag.text + next_
        qty = self._parse_text_for_quantity(qty_text.strip())
        card = self.find_card(name)
        if card.is_multifaced:
            if card in self._parsed_multifaced:
                return
            self._parsed_multifaced.add(card)
        playset = self.get_playset(card, qty)
        if self._state.is_commander:
            for card in playset:
                self._set_commander(card)
        elif self._state.is_maindeck:
            self._maindeck += playset
        elif self._state.is_sideboard:
            self._sideboard += playset

    def _parse_table(self, table: Tag) -> None:
        for row in table.find_all("tr"):
            td_tag, *_ = row.find_all("td")
            if not td_tag.text:
                continue
            a_tags = td_tag.find_all("a")
            for a_tag in a_tags:
                self._parse_tag_for_playset(a_tag)

    @override
    def _parse_input_for_decklist(self) -> None:
        current = self._deck_tag.next_sibling
        while current:
            if current.name == "p" and "Format: " in current.text and current.text.strip(
                ).lower().startswith("by "):
                author, fmt = current.text.split("Format: ", maxsplit=1)
                self._metadata["author"] = author.strip().removeprefix("By ").removeprefix("by ")
                self._update_fmt(fmt.strip())
            elif current.name in ("p", "h3") and all(t in current.text for t in "()"):
                if "Sideboard" in current.text:
                    self._state.shift_to_sideboard()
                elif not self._state.is_maindeck:
                    self._state.shift_to_maindeck()
            elif current.name == "table":
                if self._state.is_idle:
                    self._state.shift_to_commander()
                self._parse_table(current)
            elif isinstance(current, NavigableString):
                pass
            else:
                break
            current = current.next_sibling

        if self._commander:
            self._update_fmt("commander")


@HybridContainerScraper.registered
class CyclesGamingArticleScraper(HybridContainerScraper):
    """Scraper of Cycles Gaming article page.
    """
    CONTAINER_NAME = "CyclesGaming article"  # override
    DECK_TAG_PARSER_TYPE = CyclesGamingDeckTagParser  # override
    EXAMPLE_URLS = (
        "https://cyclesgaming.com/ephara-god-of-the-polis-u-w-flash/",
        "https://cyclesgaming.com/keeping-modern-janky-duskmourn-glimmers/",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return is_more_than_root_path(url)

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = super().normalize_url(url)
        return strip_url_query(url)

    @override
    def _parse_input_for_metadata(self) -> None:
        if info_tag := self._soup.find(
                "p", string=lambda s: s and "cycles" in s.lower() and ", " in s):
            author, date = info_tag.text.split(", ", maxsplit=1)
            self._metadata["author"] = author.strip().removeprefix("by ")
            with contextlib.suppress(dateutil.parser.ParserError):
                self._metadata["date"] = dateutil.parser.parse(date.strip()).date()

    @override
    def _parse_input_for_decks_data(self) -> None:
        self._deck_tags = [
            tag for tag in self._soup.find_all("h2") if "list – " in tag.text.lower()
        ]
        self._deck_urls, self._container_urls = self._find_links_in_tags(*self._soup.find_all("p"))
