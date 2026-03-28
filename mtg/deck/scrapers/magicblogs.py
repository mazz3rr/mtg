"""

    mtg.deck.scrapers.magicblogs
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape MagicBlogs.de decklists.

    @author: mazz3rr

"""
import contextlib
import logging
from typing import override

from bs4 import Tag

from mtg.constants import Json
from mtg.deck.abc import TagBasedDeckParser
from mtg.deck.scrapers.abc import HybridContainerScraper
from mtg.lib.common import ParsingError
from mtg.lib.scrape.core import ScrapingError, parse_non_english_month_date, strip_url_query

_log = logging.getLogger(__name__)


class MagicBlogsDeckTagParser(TagBasedDeckParser):
    """Parser of MagicBlogs.de decklist HTML tag.
    """
    @override
    def _parse_metadata(self) -> None:
        pass

    def _sift(self) -> list[Tag]:
        matching_tds = []
        for td in self._deck_tag.find_all('td'):
            divs = td.find_all('div', recursive=False)
            uls = td.find_all('ul', recursive=False)
            if divs and uls:
                matching_tds.append(td)
        if len(matching_tds) not in (1, 2):
            raise ParsingError(f"Unexpected number of <td> tags: {len(matching_tds)}")
        return matching_tds

    def _parse_td_tag(self, td_tag: Tag) -> None:
        for tag in td_tag.find_all(["div", "li"]):
            if tag.name == "div":
                if "Sideboard" in tag.text:
                    self._state.shift_to_sideboard()
                continue
            qty_tag = tag.find("span", class_="count")
            if qty_tag is None:
                raise ParsingError("Card quantity <span> tag not found")
            qty = int(qty_tag.text)
            name_tag = tag.find("span", class_="cardname")
            if name_tag is None:
                raise ParsingError("Card name <span> tag not found")
            name = name_tag.text
            if self._state.is_maindeck:
                self._maindeck += self.get_playset(self.find_card(name), qty)
            elif self._state.is_sideboard:
                self._sideboard += self.get_playset(self.find_card(name), qty)

    @override
    def _parse_deck(self) -> None:
        main_td, *side_td = self._sift()

        self._state.shift_to_maindeck()
        self._parse_td_tag(main_td)
        if side_td:
            self._parse_td_tag(side_td[0])

        self._derive_commander_from_sideboard()


@HybridContainerScraper.registered
class MagicBlogsArticleScraper(HybridContainerScraper):
    """Scraper of MagicBlogs.de article page.
    """
    CONTAINER_NAME = "MagicBlogs.de article"  # override
    TAG_BASED_DECK_PARSER = MagicBlogsDeckTagParser  # override
    _MONTHS = [
        'Januar',
        'Februar',
        'März',
        'April',
        'Mai',
        'Juni',
        'Juli',
        'August',
        'September',
        'Oktober',
        'November',
        'Dezember',
    ]

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return f"magicblogs.de/blog/" in url.lower()

    @staticmethod
    @override
    def sanitize_url(url: str) -> str:
        return strip_url_query(url)

    @override
    def _parse_metadata(self) -> None:
        if fmt_tag := self._soup.find("a", rel="category tag"):
            self._update_fmt(fmt_tag.text)
        self._metadata["name"] = self._soup.find("title").text
        if author_tag := self._soup.find("a", rel="author"):
            self._metadata["author"] = author_tag.text
        if time_tag := self._soup.find("time", class_="published"):
            with contextlib.suppress(ValueError):
                self._metadata["date"] = parse_non_english_month_date(time_tag.text, *self._MONTHS)

    @override
    def _collect(self) -> tuple[list[str], list[Tag], list[Json], list[str]]:
        deck_tags = [*self._soup.find_all("div", class_="mtgh")]
        main_tag = self._soup.find("section", class_="content")
        if not main_tag:
            err = ScrapingError("Article tag not found", scraper=type(self), url=self.url)
            _log.warning(f"Scraping failed with: {err!r}")
            return [], deck_tags, [], []
        deck_urls, container_urls = self._find_links_in_tags(*main_tag.find_all("p"))
        return deck_urls, deck_tags, [], container_urls
