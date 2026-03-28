"""

    mtg.deck.scrapers.wotc
    ~~~~~~~~~~~~~~~~~~~~~~
    Scrape the official WotC site's decklists.

    @author: mazz3rr

"""
import logging
import re
from typing import override

import dateutil.parser
from bs4 import Tag

from mtg.constants import Json
from mtg.deck.abc import TagBasedDeckParser
from mtg.deck.scrapers.abc import HybridContainerScraper
from mtg.scryfall import COMMANDER_FORMATS
from mtg.lib.common import ParsingError, from_iterable
from mtg.lib.scrape.core import ScrapingError, strip_url_query

_log = logging.getLogger(__name__)


# could be parsed from <script> tags' data
class WotCDeckTagParser(TagBasedDeckParser):
    """Parser of WotC decklist HTML tag.
    """
    def __init__(self, deck_tag: Tag, metadata: Json | None = None) -> None:
        super().__init__(deck_tag, metadata)
        self._locally_derived_fmt = False

    @override
    def _parse_metadata(self) -> None:
        if name := self._deck_tag.attrs.get("deck-title"):
            self._metadata["name"] = name
        if fmt := self._deck_tag.attrs.get("format"):
            if ", " in fmt:
                fmt, *_ = fmt.split(", ")
            self._update_fmt(fmt)
            self._locally_derived_fmt = True

    @staticmethod
    def _sanitize_line(line: str) -> str:
        line = line.strip()
        if line and not line[0].isdigit():
            line = "1 " + line
        # cleans gibberish in square brackets in lines like '1 Arcane Signet[45dhxuab676gfah]'
        return re.sub(r'\[[a-zA-Z0-9]+?\]', '', line).strip()

    @override
    def _parse_deck(self) -> None:
        maindeck_tag = self._deck_tag.find("main-deck")
        if not maindeck_tag:
            raise ParsingError("Decklist tag not found")

        lines = [self._sanitize_line(l) for l in maindeck_tag.text.strip().splitlines()]
        if self.fmt and self._locally_derived_fmt and self.fmt in COMMANDER_FORMATS:
            lines.insert(0, "Commander")
            lines.insert(2, "")
            lines.insert(3, "Deck")

        # haven't seen any, so let's assume one
        if sideboard_tag := self._deck_tag.find("sideboard") or self._deck_tag.find(
                "side-board") or self._deck_tag.find("side") or self._deck_tag.find("side-deck"):
            lines += ["", "Sideboard"]
            lines += [self._sanitize_line(l) for l in sideboard_tag.text.strip().splitlines()]

        self._decklist = "\n".join(lines)


_LOCALES = {"/ja/", "/fr/", "/it/", "/de/", "/es/", "/pt/", "/pt-BR/", "/ko/"}


@HybridContainerScraper.registered
class WotCArticleScraper(HybridContainerScraper):
    """Scraper of WotC article page.
    """
    CONTAINER_NAME = "WotC article"  # override
    TAG_BASED_DECK_PARSER = WotCDeckTagParser  # override

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return "magic.wizards.com/" in url.lower() and "/news/" in url.lower()

    @staticmethod
    @override
    def sanitize_url(url: str) -> str:
        locale = from_iterable(_LOCALES, lambda l: l in url)
        url = url.replace(locale, "/en/") if locale else url
        return strip_url_query(url)

    # FIXME: this isn't reached as WotC server answers with an actual 404 response
    @override
    def _is_soft_404_error(self) -> bool:
        tag = self._soup.find("h1")
        return tag and tag.text.strip() == "PAGE NOT FOUND"

    @override
    def _parse_metadata(self) -> None:
        if time_tag := self._soup.select_one("div > time"):
            self._metadata["date"] = dateutil.parser.parse(time_tag.text.strip()).date()

    @override
    def _collect(self) -> tuple[list[str], list[Tag], list[Json], list[str]]:
        deck_tags = [*self._soup.find_all("deck-list")]
        article_tag = self._soup.find("article")
        if not article_tag:
            err = ScrapingError("Article tag not found", scraper=type(self), url=self.url)
            _log.warning(f"Scraping failed with: {err!r}")
            return [], deck_tags, [], []
        p_tags = [t for t in article_tag.find_all("p") if not t.find("deck-list")]
        deck_urls, container_urls = self._find_links_in_tags(*p_tags)
        return deck_urls, deck_tags, [], container_urls
