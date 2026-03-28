"""

    mtg.deck.scrapers.coolstuff
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape CoolStuffInc decklists.

    @author: mazz3rr

"""
import logging
from typing import override

import dateutil.parser
from bs4 import Tag

from mtg.constants import Json, SECRETS
from mtg.deck.core import DeckParser
from mtg.deck.abc import TagBasedDeckParser
from mtg.deck.scrapers.abc import HybridContainerScraper
from mtg.lib.common import ParsingError, extract_int
from mtg.lib.scrape.core import ScrapingError, is_more_than_root_path, strip_url_query
from mtg.yt.discover import UrlHook

_log = logging.getLogger(__name__)


HEADERS = {
    "Host": "www.coolstuffinc.com",
    "User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:137.0) Gecko/20100101 Firefox/137.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "DNT": "1",
    "Sec-GPC": "1",
    "Connection": "keep-alive",
    "Cookie": SECRETS["coolstuff"]["cookie"],
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "cross-site",
    "Priority": "u=0, i",
}
URL_PREFIX = "https://www.coolstuffinc.com"
URL_HOOKS = (
    # article & author
    UrlHook(
        ('"coolstuffinc.com/a/"', ),
    ),
)


class _SubParser(TagBasedDeckParser):
    """A sub-parser to handle cases where there's no 'Export text' button.
    """
    @override
    def _parse_metadata(self) -> None:
        pass

    @override
    def _parse_deck(self) -> None:
        for li_tag in self._deck_tag.find_all("li"):
            if li_tag.attrs.get("class") == ["card-type"]:
                if "Commander (" in li_tag.text.strip():
                    self._state.shift_to_commander()
                elif "Companion (" in li_tag.text.strip():
                    self._state.shift_to_companion()
                elif "Sideboard (" in li_tag.text.strip():
                    self._state.shift_to_sideboard()
                else:
                    if not self._state.is_maindeck:
                        self._state.shift_to_maindeck()
            else:
                qty, name = li_tag.text.strip().split(maxsplit=1)
                playset = self.get_playset(self.find_card(name), int(qty))
                if self._state.is_maindeck:
                    self._maindeck += playset
                elif self._state.is_sideboard:
                    self._sideboard += playset
                elif self._state.is_commander:
                    self._set_commander(playset[0])
                elif self._state.is_companion:
                    self._companion = playset[0]


class CoolStuffIncDeckTagParser(TagBasedDeckParser):
    """Parser of an CoolStuffInc decklist HTML tag.
    """
    @property
    def commander_count(self) -> int:
        commander_tag = self._deck_tag.find(
            "li", class_="card-type", string=lambda s: s and "Commander (" in s)
        if commander_tag:
            return extract_int(commander_tag.text.strip())
        return 0

    @override
    def _parse_metadata(self) -> None:
        info_tag = self._deck_tag.find("h4")
        if not info_tag:
            raise ParsingError("Metadata tag not found")

        name, fmt, author, rest = "", "", "", []
        if "|" not in info_tag.text:
            name = info_tag.text.strip()
            fmt = self.derive_format_from_text(name)
        else:
            parts = info_tag.text.strip().split(" | ")
            if len(parts) == 2:
                name, author = parts
                fmt = self.derive_format_from_text(name)
            elif len(parts) == 3:
                name, fmt_text, author = parts
                fmt = self.derive_format_from_text(fmt_text)
            elif len(parts) > 3:
                name, fmt_text, author, *rest = parts
                fmt = self.derive_format_from_text(fmt_text)

        self._metadata["name"] = name.strip()
        if fmt:
            self._update_fmt(fmt)
        if author:
            if ", " in author:
                author, event = author.split(", ", maxsplit=1)
                self._metadata["event"] = event
            self._metadata["author"] = author.strip()
        if rest:
            self._metadata["event"] = "".join(rest)

        if date := self._metadata.get("article", {}).get("date"):
            self._metadata["date"] = date

    @override
    def _parse_deck(self) -> None:
        decklist_tag = self._deck_tag.find("a", {"data-reveal-id": "copydecklistmodal"})
        if not decklist_tag or not decklist_tag.attrs.get("data-text"):
            sub_tag = self._deck_tag.select_one("div.card-list")
            if not sub_tag:
                raise ParsingError("Decklist tag not found")
            self._sub_parser = _SubParser(sub_tag, self._metadata)

        else:
            decklist_text = decklist_tag.attrs["data-text"].strip()
            if "|~|" in decklist_text:
                maindeck, sideboard = decklist_text.split("|~|")
            else:
                maindeck, sideboard = decklist_text, ""
            maindeck = maindeck.split("|")
            sideboard = sideboard.split("|") if sideboard else []
            if self.commander_count:
                commander, maindeck = maindeck[
                                      :self.commander_count], maindeck[self.commander_count:]
            else:
                commander, maindeck = [], maindeck
            decklist = []
            if commander:
                decklist.append("Commander")
                decklist.extend(commander)
                decklist.append("")
            decklist.append("Deck")
            decklist += maindeck
            if sideboard:
                decklist.append("")
                decklist.append("Sideboard")
                decklist.extend(sideboard)
            self._decklist = "\n".join(decklist)

    @override
    def _get_sub_parser(self) -> DeckParser | None:
        if self._sub_parser:
            return self._sub_parser
        return super()._get_sub_parser()


@HybridContainerScraper.registered
class CoolStuffIncArticleScraper(HybridContainerScraper):
    """Scraper of CoolStuffInc article page.
    """
    CONTAINER_NAME = "CoolStuffInc article"  # override
    HEADERS = HEADERS  # override
    TAG_BASED_DECK_PARSER = CoolStuffIncDeckTagParser  # override

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return is_more_than_root_path(
            url, "coolstuffinc.com/a/") and "action=search" not in url.lower()

    @staticmethod
    @override
    def sanitize_url(url: str) -> str:
        return strip_url_query(url)

    @override
    def _parse_metadata(self) -> None:
        if header_tag := self._soup.find("header"):
            if title_tag := header_tag.find("h1"):
                self._metadata.setdefault("article", {})["title"] = title_tag.text.strip()
            if byline_tag := header_tag.find("div", class_="gm-article-byline"):
                if author_tag := byline_tag.find("a"):
                    self._metadata.setdefault("article", {})["author"] = author_tag.text.strip()
                if date_tag := byline_tag.find("div", string=lambda s: s and "Posted on " in s):
                    self._metadata.setdefault("article", {})["date"] = dateutil.parser.parse(
                        date_tag.text.strip().removeprefix("Posted on ")).date()

    @override
    def _collect(self) -> tuple[list[str], list[Tag], list[Json], list[str]]:
        deck_tags = [*self._soup.select("div.gm-deck")]
        article_tag = self._soup.find("article")
        if not article_tag:
            err = ScrapingError("Article tag not found", scraper=type(self), url=self.url)
            _log.warning(f"Scraping failed with: {err!r}")
            return [], deck_tags, [], []
        deck_urls, container_urls = self._find_links_in_tags(*article_tag.find_all("p"))
        return deck_urls, deck_tags, [], container_urls


@HybridContainerScraper.registered
class CoolStuffIncAuthorScraper(HybridContainerScraper):
    """Scraper of CoolStuffInc author page.
    """
    CONTAINER_NAME = "CoolStuffInc author"  # override
    HEADERS = HEADERS  # override
    CONTAINER_SCRAPERS = CoolStuffIncArticleScraper,  # override

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        tokens = "coolstuffinc.com/a/", "action=search", "author"
        return all(t in url.lower() for t in tokens)

    @override
    def _collect(self) -> tuple[list[str], list[Tag], list[Json], list[str]]:
        search_tag = self._soup.select_one("div#article-search-results")
        if not search_tag:
            raise ScrapingError("Search results tag not found", scraper=type(self), url=self.url)
        _, container_urls = self._find_links_in_tags(search_tag, url_prefix=URL_PREFIX)
        return [], [], [], container_urls
