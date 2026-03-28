"""

    mtg.deck.scrapers.draftsim
    ~~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape Draftsim decklists.

    @author: mazz3rr

"""
import logging
from typing import Type, override

import dateutil.parser
from bs4 import Tag

from mtg.constants import Json
from mtg.deck.abc import TagBasedDeckParser
from mtg.deck.scrapers.abc import DeckScraper, HybridContainerScraper
from mtg.lib.common import ParsingError, from_iterable, get_date_from_ago_text
from mtg.lib.scrape.core import ScrapingError, is_more_than_root_path, strip_url_query
from mtg.scryfall import all_formats
from mtg.yt.discover import UrlHook

_log = logging.getLogger(__name__)
URL_HOOKS = (
    # deck & article & author
    UrlHook(
        ('"draftsim.com/"', ),
        ('-"/blog"', '-"/ratings"', '-"/all-sets"', '-"/arenatutor"'),
    ),
)


@DeckScraper.registered
class DraftsimDeckScraper(DeckScraper):
    """Scraper of Draftsim decklist page.
    """
    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return is_more_than_root_path(url, "draftsim.com/decks/")

    @staticmethod
    @override
    def sanitize_url(url: str) -> str:
        return strip_url_query(url)

    @override
    def _parse_metadata(self) -> None:
        if info_tag := self._soup.find("div", class_="deckstats__overview__left"):
            for info_text in info_tag.text.strip().split("\n"):
                info_text = info_text.strip()
                if info_text.startswith("Deck format: "):
                    self._update_fmt(info_text.removeprefix("Deck format: "))
                elif info_text.startswith("Added: "):
                    self._metadata["date"] = dateutil.parser.parse(
                        info_text.removeprefix("Added: ")).date()

    @override
    def _parse_deck(self) -> None:
        decklist_tag = self._soup.find("textarea", id="decktext")
        if not decklist_tag:
            raise ScrapingError("Decklist tag not found", scraper=type(self), url=self.url)
        self._decklist = decklist_tag.text.strip()


class DraftsimDeckTagParser(TagBasedDeckParser):
    """Parser of a Draftsim article's decklist HTML tag.
    """
    @override
    def _parse_metadata(self) -> None:
        name = None
        if form_tag := self._deck_tag.find("form"):
            if name := form_tag.attrs.get("data-deck-title"):
                self._metadata["name"] = name
            else:
                current = self._deck_tag.previous_sibling
                while current:
                    if current.name == "h2":
                        name = current.text.strip()
                        self._metadata["name"] = name
                        break
                    current = current.previous_sibling

        if name:
            parts = [p.lower() for p in name.split()]
            if fmt := from_iterable(parts, lambda p: p in all_formats()):
                self._update_fmt(fmt)

    @staticmethod
    def _derive_deck_pos(decklist: list[str]) -> int:
        if "Commander" not in decklist and "Companion" not in decklist:
            return 0
        if "Companion" in decklist:
            idx = decklist.index("Companion")
            if idx == 0:
                raise ParsingError("Unexpected decklist format")
            return idx + 2
        idx = decklist.index("Commander")
        if idx != 0:
            raise ParsingError("Unexpected decklist format")
        if not decklist[2]:
            return 3
        if not decklist[3]:
            return 4
        else:
            raise ParsingError("Unexpected decklist format")

    @override
    def _parse_deck(self) -> None:
        decklist = [l.strip() for l in self._deck_tag.text.strip().split("\n")]
        decklist.insert(self._derive_deck_pos(decklist), "Deck")
        self._decklist = "\n".join(decklist)


@HybridContainerScraper.registered
class DraftsimArticleScraper(HybridContainerScraper):
    """Scraper of Draftsim article page.
    """
    CONTAINER_NAME = "Draftsim article"  # override
    TAG_BASED_DECK_PARSER = DraftsimDeckTagParser  # override

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        tokens = "/decks/", "/author/", "/blog", "/ratings/", "/all-sets", "/arenatutor"
        return is_more_than_root_path(url, "draftsim.com") and not any(
            t in url.lower() for t in tokens)

    @staticmethod
    @override
    def sanitize_url(url: str) -> str:
        return strip_url_query(url)

    @override
    def _parse_metadata(self) -> None:
        if info_tag := self._soup.find("div", class_="post_info"):
            info_text = info_tag.text.strip()
            author_text, dt_text, tags_text = None, None, None
            if info_text.count("\n") == 2:
                author_text, dt_text, tags_text = info_text.split("\n")
            elif info_text.count("\n") == 1:
                author_text, dt_text = info_text.split("\n")
            elif info_text.count("\n") == 0:
                author_text = info_text
            if author_text:
                self._metadata["author"] = author_text.strip()
            if dt_text:
                try:
                    dt_text = dt_text.strip()
                    self._metadata["date"] = dateutil.parser.parse(dt_text).date()
                except dateutil.parser.ParserError:
                    if "ago" in dt_text:
                        self._metadata["date"] = get_date_from_ago_text(dt_text)
            if tags_text and ", " in tags_text:
                tags = tags_text.strip().split(", ")
                if fmt := from_iterable(tags, lambda t: t.lower() in all_formats()):
                    self._update_fmt(fmt)
                self._metadata["article_tags"] = [t.lower() for t in tags if t]

    @override
    def _collect(self) -> tuple[list[str], list[Tag], list[Json], list[str]]:
        deck_tags = [*self._soup.find_all("div", class_="deck_list")]
        article_tag = self._soup.find("article")
        if not article_tag:
            err = ScrapingError("Article tag not found", scraper=type(self), url=self.url)
            _log.warning(f"Scraping failed with: {err!r}")
            return [], deck_tags, [], []
        p_tags = [t for t in article_tag.find_all("p") if not t.find("div", class_="deck_list")]
        deck_urls, container_urls = self._find_links_in_tags(*p_tags)
        return deck_urls, deck_tags, [], container_urls


@HybridContainerScraper.registered
class DraftsimAuthorScraper(HybridContainerScraper):
    """Scraper of Draftsim author page.
    """
    CONTAINER_NAME = "Draftsim author"  # override
    CONTAINER_SCRAPERS = DraftsimArticleScraper,  # override

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return "draftsim.com/" in url.lower() and "/author/" in url.lower()

    @staticmethod
    @override
    def sanitize_url(url: str) -> str:
        return strip_url_query(url)

    @classmethod
    @override
    def _get_deck_scrapers(cls) -> set[Type[DeckScraper]]:
        return set()

    @override
    def _collect(self) -> tuple[list[str], list[Tag], list[Json], list[str]]:
        content_tag = self._soup.select_one("div.content")
        if not content_tag:
            raise ScrapingError("Content tag not found", scraper=type(self), url=self.url)
        h3_tags = [t for t in content_tag.select("h3.post_title")]
        _, container_urls = self._find_links_in_tags(*h3_tags)
        return [], [], [], container_urls
