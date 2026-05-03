"""

    mtg.deck.scrapers.edhrec
    ~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape EDHREC decklists.

    @author: mazz3rr

"""
import json
import logging
import re
from datetime import datetime
from typing import Type, override

import dateutil.parser
from bs4 import BeautifulSoup, Tag

from mtg.constants import Json
from mtg.deck.abc import DeckTagParser
from mtg.deck.scrapers.abc import DeckScraper, HybridContainerScraper
from mtg.lib.common import ParsingError
from mtg.lib.scrape.core import (
    ScrapingError, fetch_soup, find_links, normalize_url, prepend_url,
    strip_url_query,
)
from mtg.yt.discover import UrlHook

_log = logging.getLogger(__name__)
URL_PREFIX = "https://edhrec.com"
URL_HOOKS = (
    # deck preview
    UrlHook(
        ('"edhrec.com/"', '"/deckpreview/"'),
    ),
    # average deck #1
    UrlHook(
        ('"edhrec.com/"', '"/average-decks/"'),
        ('-"/month"', ),
    ),
    # average deck #2
    UrlHook(
        ('"edhrec.com/"', '"/commanders/"'),
        ('-"/month"', ),
    ),
    # article & author & article search #1
    UrlHook(
        ('"edhrec.com/articles/"', ),
    ),
    # article & author & article search #2
    UrlHook(
        ('"articles.edhrec.com/"', ),
    ),
)


def _get_data(
        url: str,
        scraper: Type[DeckScraper] | Type[HybridContainerScraper],
        data_key="data") -> tuple[Json, BeautifulSoup]:
    soup = fetch_soup(url)
    if not soup:
        raise ScrapingError(scraper=scraper, url=url)
    script_tag = soup.find("script", id="__NEXT_DATA__")
    try:
        data = json.loads(script_tag.text)
        deck_data = data["props"]["pageProps"][data_key]
    except (AttributeError, KeyError):
        raise ScrapingError(
            "Failed data extraction from <script> tag's JavaScript", scraper=scraper, url=url)
    return deck_data, soup


@DeckScraper.registered
class EdhrecPreviewDeckScraper(DeckScraper):
    """Scraper of EDHREC preview decklist page.
    """
    EXAMPLE_URLS = (
        "https://edhrec.com/deckpreview/KOhEueaPVFI7Zj0pqTn6SA",
        "https://edhrec.com/deckpreview/eGjbKhdKXZIK473Lqbybvg",
        "https://edhrec.com/deckpreview/9mZz3h1gZINrHfwoDGCgKQ",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        url = url.lower().strip()
        return "edhrec.com/" in url and "/deckpreview/" in url

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = normalize_url(url, case_sensitive=True)
        return strip_url_query(url)

    @override
    def _pre_parse(self) -> None:
        self._json, self._soup = _get_data(self.url, type(self))

    @override
    def _parse_input_for_metadata(self) -> None:
        self._update_fmt("commander")
        self._metadata["date"] = datetime.fromisoformat(self._json["savedate"]).date()
        if header := self._json.get("header"):
            self._metadata["name"] = header
        self._metadata["is_cedh"] = self._json["cedh"]
        if edhrec_tags := self._json.get("edhrec_tags"):
            self._metadata["edhrec_tags"] = edhrec_tags
        if tags := self._json.get("tags"):
            self._metadata["tags"] = self.normalize_metadata_deck_tags(tags)
        if salt := self._json.get("salt"):
            self._metadata["salt"] = salt
        if theme := self._json.get("theme"):
            self._metadata["theme"] = theme
        if tribe := self._json.get("tribe"):
            self._metadata["tribe"] = tribe

    @override
    def _parse_input_for_decklist(self) -> None:
        decklist = ["Commander"]
        decklist += [f"1 {playset}" for playset in self._json["commanders"] if playset]
        decklist += ["", "Deck"]
        decklist += [playset for playset in self._json["deck"] if playset not in decklist]
        self._decklist = "\n".join(decklist)


@DeckScraper.registered
class EdhrecAverageDeckScraper(DeckScraper):
    """Scraper of EDHREC average decklist page and commander page.
    """
    # those are collected by the article scraper
    # but doesn't work even when trying manually in a browser
    _BAD_URLS = {
        'https://edhrec.com/average-decks/abzan',
        'https://edhrec.com/average-decks/azorius',
        'https://edhrec.com/average-decks/bant',
        'https://edhrec.com/average-decks/boros',
        'https://edhrec.com/average-decks/colorless',
        'https://edhrec.com/average-decks/dimir',
        'https://edhrec.com/average-decks/dune-brood',
        'https://edhrec.com/average-decks/esper',
        'https://edhrec.com/average-decks/five-color',
        'https://edhrec.com/average-decks/glint-eye',
        'https://edhrec.com/average-decks/golgari',
        'https://edhrec.com/average-decks/grixis',
        'https://edhrec.com/average-decks/gruul',
        'https://edhrec.com/average-decks/ink-treader',
        'https://edhrec.com/average-decks/izzet',
        'https://edhrec.com/average-decks/jeskai',
        'https://edhrec.com/average-decks/jund',
        'https://edhrec.com/average-decks/mardu',
        'https://edhrec.com/average-decks/mono-black',
        'https://edhrec.com/average-decks/mono-blue',
        'https://edhrec.com/average-decks/mono-green',
        'https://edhrec.com/average-decks/mono-red',
        'https://edhrec.com/average-decks/mono-white',
        'https://edhrec.com/average-decks/naya',
        'https://edhrec.com/average-decks/orzhov',
        'https://edhrec.com/average-decks/rakdos',
        'https://edhrec.com/average-decks/selesnya',
        'https://edhrec.com/average-decks/simic',
        'https://edhrec.com/average-decks/sultai',
        'https://edhrec.com/average-decks/temur',
        'https://edhrec.com/average-decks/witch-maw',
        'https://edhrec.com/average-decks/yore-tiller',
    }
    EXAMPLE_URLS = (
        "https://edhrec.com/average-decks/honest-rutstein",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        url = url.lower()
        if url in cls._BAD_URLS:
            return False
        return (
            "edhrec.com/" in url
            and ("/average-decks/" in url or "/commanders/" in url)
            and "/month" not in url
        )

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = super().normalize_url(url)
        url = strip_url_query(url)
        return url.replace("/commanders/", "/average-decks/")

    @override
    def _is_soft_404_error(self) -> bool:
        return self._soup.find(
            "h2", string=lambda s: s and s.strip() == "404 Page Not Found") is not None

    @override
    def _pre_parse(self) -> None:
        self._json, self._soup = _get_data(self.url, type(self))

    @override
    def _parse_input_for_metadata(self) -> None:
        self._update_fmt("commander")
        self._metadata["date"] = datetime.today().date()
        if header := self._json.get("header"):
            self._metadata["name"] = header

    @override
    def _parse_input_for_decklist(self) -> None:
        for i, card_text in enumerate(self._json["deck"]):
            qty, card_name = card_text.split(maxsplit=1)
            card = self.find_card(card_name)
            if i == 0:
                self._set_commander(card)
            else:
                if card.is_partner:
                    self._set_commander(card)
                else:
                    self._maindeck += self.get_playset(card, int(qty))


class EdhrecDeckTagParser(DeckTagParser):
    """Parser of an EDHREC decklist HTML tag (that lives inside an article's <script> JSON data).
    """
    @override
    def _parse_input_for_metadata(self) -> None:
        if name := self._deck_tag.attrs.get("name"):
            self._metadata["name"] = name

    @staticmethod
    def _clean_decklist(decklist: str) -> str:
        # remove category tags and keep content between them
        # matches patterns like [Category]content[/Category]
        cleaned = re.sub(r'\[/?[\w\s!]+\]\n?', '', decklist)
        # remove leading/trailing whitespace and asterisks
        lines = [line.strip().lstrip('*') for line in cleaned.splitlines()]
        return '\n'.join([f"1{l}" if l.startswith(" ") else l for l in lines])

    @staticmethod
    def _handle_commander(decklist: str) -> str:
        if "[/Commander]\n" in decklist:
            prefix, decklist = decklist.split("[/Commander]\n", maxsplit=1)
            return "Commander" + prefix.removeprefix("[Commander]") + f"\nDeck\n{decklist}"
        return decklist

    @override
    def _parse_input_for_decklist(self) -> None:
        cards_text = self._deck_tag.attrs.get("cards")
        if not cards_text:
            raise ParsingError("Text decklist missing from deck tag's attributes")
        decklist = self._handle_commander(cards_text)
        self._decklist = self._clean_decklist(decklist)


@HybridContainerScraper.registered
class EdhrecArticleScraper(HybridContainerScraper):
    """Scraper of EDHREC article page.
    """
    CONTAINER_NAME = "EDHREC article"  # override
    DECK_TAG_PARSER_TYPE = EdhrecDeckTagParser  # override
    EXAMPLE_URLS = (
        "https://edhrec.com/articles/living-energy-precon-review-aetherdrift",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return (
            ("edhrec.com/articles/" in url.lower() or "articles.edhrec.com/" in url.lower())
            and "/author/" not in url.lower()
            and "/search/" not in url.lower()
        )

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = super().normalize_url(url)
        return strip_url_query(url)

    @override
    def _pre_parse(self) -> None:
        self._json, self._soup = _get_data(self.url, type(self), data_key="post")

    @override
    def _parse_input_for_metadata(self) -> None:
        self._update_fmt("commander")
        if author := self._json.get("author", {}).get("name"):
            self._metadata["author"] = author
        if date := self._json.get("date"):
            self._metadata["date"] = dateutil.parser.parse(date).date()
        if excerpt := self._json.get("excerpt"):
            self._metadata.setdefault("article", {})["excerpt"] = excerpt
        if title := self._json.get("title"):
            self._metadata.setdefault("article", {})["title"] = title
        if tags := self._json.get("tags"):
            self._metadata["tags"] = self.normalize_metadata_deck_tags(tags)

    def _collect_tags(self) -> list[Tag]:
        content_soup = BeautifulSoup(self._json["content"], "lxml")
        return [*content_soup.find_all("span", class_="edhrecp__deck-s")]

    def _collect_urls(self) -> tuple[list[str], list[str]]:
        links = find_links(self._soup)
        tokens = "/deckpreview/", "/average-decks/", "/commanders/"
        links = [
            prepend_url(l, URL_PREFIX) if any(
                l.startswith(t) for t in tokens) else l for l in links]
        return self._sift_links(*links)

    @override
    def _parse_input_for_decks_data(self) -> None:
        self._deck_tags = self._collect_tags()
        self._deck_urls, self._container_urls = self._collect_urls()


@HybridContainerScraper.registered
class EdhrecAuthorScraper(HybridContainerScraper):
    """Scraper of EDHREC author page.
    """
    CONTAINER_NAME = "EDHREC author"  # override
    CONTAINER_SCRAPER_TYPES = EdhrecArticleScraper,  # override
    EXAMPLE_URLS = (
        "https://edhrec.com/articles/author/angelo-guerrera",
        "https://articles.edhrec.com/author/joseph-schultz",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return (
            ("edhrec.com/articles/" in url.lower() or "articles.edhrec.com/" in url.lower())
            and "/author/" in url.lower()
        )

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = super().normalize_url(url)
        return strip_url_query(url)

    @override
    def _pre_parse(self) -> None:
        self._json, self._soup = _get_data(self.url, type(self), data_key="posts")

    @override
    def _parse_input_for_decks_data(self) -> None:
        prefix = f'{URL_PREFIX}/articles/'
        self._container_urls = [prepend_url(d["slug"], prefix) for d in self._json]


@HybridContainerScraper.registered
class EdhrecArticleSearchScraper(EdhrecAuthorScraper):
    """Scraper of EDHREC article search page.
    """
    CONTAINER_NAME = "EDHREC article search"  # override
    EXAMPLE_URLS = (
        "https://edhrec.com/articles/search/tyler%20bucks",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return (
            ("edhrec.com/articles/" in url.lower() or "articles.edhrec.com/" in url.lower())
            and "/search/" in url.lower()
        )
