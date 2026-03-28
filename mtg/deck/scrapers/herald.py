"""

    mtg.deck.scrapers.herald
    ~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape Commander's Herald decklists.

    @author: mazz3rr

"""
import logging
from typing import override

import dateutil.parser
from bs4 import Tag

from mtg.constants import Json, SECRETS
from mtg.deck.abc import TagBasedDeckParser
from mtg.deck.scrapers.abc import HybridContainerScraper
from mtg.lib.scrape.core import ScrapingError, is_more_than_root_path, strip_url_query
from mtg.yt.discover import UrlHook

_log = logging.getLogger(__name__)
HEADERS = {
    "Host": "commandersherald.com",
    "User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:141.0) Gecko/20100101 Firefox/141.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Sec-GPC": "1",
    "Connection": "keep-alive",
    "Cookie": SECRETS["herald"]["cookie"],
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "cross-site",
    "Priority": "u=0, i",
}


URL_HOOKS = (
    # article & author
    UrlHook(
        ('"commandersherald.com/"', ),
        ('-"/all-edh-deck-guides"', '-"/articles"', '-"/cedh-deck-guides"', '-"/games/"',
         '-"/category/"', '-"/about-us"','-"/contact-us"', '-"/privacy-policy"',
         '-"/terms-of-service"'),
    ),
)


class CommandersHeraldDeckTagParser(TagBasedDeckParser):
    """Parser of a Commander's Herald decklist HTML tag.
    """
    @override
    def _parse_metadata(self) -> None:
        self._metadata["name"] = self._deck_tag.attrs["name"]
        if author := self._metadata.get("article", {}).get("author"):
            self._metadata["author"] = author
        if date := self._metadata.get("article", {}).get("date"):
            self._metadata["date"] = date
        if cats := self._metadata.get("article", {}).get("tags"):
            fmt = self.derive_format_from_words(*cats)
            self._update_fmt(fmt or "commander")

    @override
    def _parse_deck(self) -> None:
        decklist = self._deck_tag.attrs["cards"]
        lines = ["Commander"]
        for line in decklist.splitlines():
            if line.startswith("*"):
                lines.append(line[1:])
            elif line == "[/Commander]":
                lines += ["", "Deck"]
        self._decklist = "\n".join(lines)


@HybridContainerScraper.registered
class CommandersHeraldArticleScraper(HybridContainerScraper):
    """Scraper of Commander's Herald article page.
    """
    CONTAINER_NAME = "Commander's Herald article"  # override
    TAG_BASED_DECK_PARSER = CommandersHeraldDeckTagParser  # override
    HEADERS = HEADERS  # override

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        tokens = ('/all-edh-deck-guides', '/articles', '/author/', '/cedh-deck-guides',
                  '/games/', "/category/", '/about-us','/contact-us', '/privacy-policy',
                  '/terms-of-service')
        return is_more_than_root_path(
            url, "commandersherald.com") and not any(t in url.lower() for t in tokens)

    @staticmethod
    @override
    def sanitize_url(url: str) -> str:
        return strip_url_query(url)

    @override
    def _parse_metadata(self) -> None:
        if header_tag := self._soup.select_one("header.article-header"):
            if title_tag := header_tag.find("h1"):
                self._metadata.setdefault("article", {})["title"] = title_tag.text.strip()
            if author_tag := header_tag.select_one("div.author-meta"):
                if p_tag := author_tag.find("p"):
                    if " • " in p_tag.text.strip():
                        author, date_text = p_tag.text.strip().split(" • ", maxsplit=1)
                        self._metadata.setdefault("article", {})["author"] = author.strip()
                        self._metadata.setdefault("article", {})["date"] = dateutil.parser.parse(
                            date_text.strip()).date()
                if categories := [a.text.strip() for a in author_tag.select("a.badge")]:
                    self._metadata.setdefault(
                "article", {})["tags"] = self.sanitize_metadata_deck_tags(categories)

    @override
    def _collect(self) -> tuple[list[str], list[Tag], list[Json], list[str]]:
        deck_tags = [
            tag.find("span") for tag in self._soup.select("div.edhrecp__deck.mtgh")
            if tag.find("span")]
        article_tag = self._soup.select_one("section.article-content")
        if not article_tag:
            err = ScrapingError("Article tag not found", scraper=type(self), url=self.url)
            _log.warning(f"Scraping failed with: {err!r}")
            return [], deck_tags, [], []
        deck_urls, container_urls = self._find_links_in_tags(*article_tag.find_all("p"))
        return deck_urls, deck_tags, [], container_urls


@HybridContainerScraper.registered
class CommandersHeraldAuthorScraper(HybridContainerScraper):
    """Scraper of Commander's Herald author page.
    """
    CONTAINER_NAME = "Commander's Herald author"  # override
    CONTAINER_SCRAPERS = CommandersHeraldArticleScraper,  # override
    HEADERS = HEADERS  # override

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return "commandersherald.com/author/" in url.lower()

    @override
    def _collect(self) -> tuple[list[str], list[Tag], list[Json], list[str]]:
        _, container_urls = self._find_links_in_tags(css_selector="div > div > h3 > a")
        return [], [], [], container_urls
