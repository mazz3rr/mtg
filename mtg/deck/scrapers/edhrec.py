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
from mtg.deck.abc import TagBasedDeckParser
from mtg.deck.scrapers.abc import DeckScraper, HybridContainerScraper
from mtg.lib.common import ParsingError
from mtg.lib.scrape.core import ScrapingError, fetch_soup, find_links, prepend_url, \
    strip_url_query
from mtg.scryfall import Card
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
    COLORS_TO_BASIC_LANDS = {
        "W": "Plains",
        "U": "Island",
        "B": "Swamp",
        "R": "Mountain",
        "G": "Forest",
    }

    @property
    def cards(self) -> list[Card]:
        cards = []
        if self._commander:
            cards.append(self._commander)
        if self._partner_commander:
            cards.append(self._partner_commander)
        cards += self._maindeck
        return cards

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return "edhrec.com/" in url.lower() and "/deckpreview/" in url.lower()

    @staticmethod
    @override
    def sanitize_url(url: str) -> str:
        return strip_url_query(url)

    @override
    def _pre_parse(self) -> None:
        self._data, self._soup = _get_data(self.url, type(self))

    @override
    def _parse_metadata(self) -> None:
        self._update_fmt("commander")
        self._metadata["date"] = datetime.fromisoformat(self._data["savedate"]).date()
        if header := self._data.get("header"):
            self._metadata["name"] = header
        self._metadata["is_cedh"] = self._data["cedh"]
        if edhrec_tags := self._data.get("edhrec_tags"):
            self._metadata["edhrec_tags"] = edhrec_tags
        if tags := self._data.get("tags"):
            self._metadata["tags"] = self.sanitize_metadata_deck_tags(tags)
        if salt := self._data.get("salt"):
            self._metadata["salt"] = salt
        if theme := self._data.get("theme"):
            self._metadata["theme"] = theme
        if tribe := self._data.get("tribe"):
            self._metadata["tribe"] = tribe

    def _add_basic_lands(self) -> None:
        lands = [self.COLORS_TO_BASIC_LANDS[c] for c in self._data["coloridentity"]]
        pool = [self.find_card(l) for l in lands]
        cursor = 0
        while len(self.cards) < 100:
            self._maindeck.append(pool[cursor])
            cursor += 1
            if cursor == len(pool):
                cursor = 0

    @override
    def _parse_deck(self) -> None:
        for card_name in self._data["cards"]:
            self._maindeck += self.get_playset(self.find_card(card_name), 1)

        for card_name in [c for c in self._data["commanders"] if c]:
            card = self.find_card(card_name)
            self._set_commander(card)

        self._add_basic_lands()


@DeckScraper.registered
class EdhrecAverageDeckScraper(DeckScraper):
    """Scraper of EDHREC average decklist page and commander page.
    """
    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return ("edhrec.com/" in url.lower()
                and ("/average-decks/" in url.lower() or "/commanders/" in url.lower())
                and "/month" not in url.lower())

    @staticmethod
    @override
    def sanitize_url(url: str) -> str:
        url = strip_url_query(url)
        return url.replace("/commanders/", "/average-decks/")

    # TODO: check why this doesn't work (url: https://edhrec.com/average-decks/yore-tiller)
    @override
    def _is_soft_404_error(self) -> bool:
        return self._soup.find(
            "h2", string=lambda s: s and s.strip() == "404 Page Not Found") is not None

    @override
    def _pre_parse(self) -> None:
        self._data, self._soup = _get_data(self.url, type(self))

    @override
    def _parse_metadata(self) -> None:
        self._update_fmt("commander")
        self._metadata["date"] = datetime.today().date()
        if header := self._data.get("header"):
            self._metadata["name"] = header

    @override
    def _parse_deck(self) -> None:
        for i, card_text in enumerate(self._data["deck"]):
            qty, card_name = card_text.split(maxsplit=1)
            card = self.find_card(card_name)
            if i == 0:
                self._set_commander(card)
            else:
                if card.is_partner:
                    self._set_commander(card)
                else:
                    self._maindeck += self.get_playset(card, int(qty))


class EdhrecDeckTagParser(TagBasedDeckParser):
    """Parser of an EDHREC decklist HTML tag (that lives inside an article's <script> JSON data).
    """
    @override
    def _parse_metadata(self) -> None:
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
    def _parse_deck(self) -> None:
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
    TAG_BASED_DECK_PARSER = EdhrecDeckTagParser  # override

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return (("edhrec.com/articles/" in url.lower()
                 or "articles.edhrec.com/" in url.lower())
                and "/author/" not in url.lower() and "/search/" not in url.lower())

    @staticmethod
    @override
    def sanitize_url(url: str) -> str:
        return strip_url_query(url)

    @override
    def _pre_parse(self) -> None:
        self._data, self._soup = _get_data(self.url, type(self), data_key="post")

    @override
    def _parse_metadata(self) -> None:
        self._update_fmt("commander")
        if author := self._data.get("author", {}).get("name"):
            self._metadata["author"] = author
        if date := self._data.get("date"):
            self._metadata["date"] = dateutil.parser.parse(date).date()
        if excerpt := self._data.get("excerpt"):
            self._metadata.setdefault("article", {})["excerpt"] = excerpt
        if title := self._data.get("title"):
            self._metadata.setdefault("article", {})["title"] = title
        if tags := self._data.get("tags"):
            self._metadata["tags"] = self.sanitize_metadata_deck_tags(tags)

    def _collect_tags(self) -> list[Tag]:
        content_soup = BeautifulSoup(self._data["content"], "lxml")
        return [*content_soup.find_all("span", class_="edhrecp__deck-s")]

    def _collect_urls(self) -> tuple[list[str], list[str]]:
        links = find_links(self._soup)
        tokens = "/deckpreview/", "/average-decks/", "/commanders/"
        links = [
            prepend_url(l, URL_PREFIX) if any(
                l.startswith(t) for t in tokens) else l for l in links]
        return self._sift_links(*links)

    @override
    def _collect(self) -> tuple[list[str], list[Tag], list[Json], list[str]]:
        deck_urls, container_urls = self._collect_urls()
        return deck_urls, self._collect_tags(), [], container_urls


@HybridContainerScraper.registered
class EdhrecAuthorScraper(HybridContainerScraper):
    """Scraper of EDHREC author page.
    """
    CONTAINER_NAME = "EDHREC author"  # override
    CONTAINER_SCRAPERS = EdhrecArticleScraper,  # override

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return (("edhrec.com/articles/" in url.lower()
                 or "articles.edhrec.com/" in url.lower()) and "/author/" in url.lower())

    @staticmethod
    @override
    def sanitize_url(url: str) -> str:
        return strip_url_query(url)

    @override
    def _pre_parse(self) -> None:
        self._data, self._soup = _get_data(self.url, type(self), data_key="posts")

    @override
    def _collect(self) -> tuple[list[str], list[Tag], list[Json], list[str]]:
        prefix = f'{URL_PREFIX}/articles/'
        return [], [], [], [prepend_url(d["slug"], prefix) for d in self._data]


@HybridContainerScraper.registered
class EdhrecArticleSearchScraper(EdhrecAuthorScraper):
    """Scraper of EDHREC article search page.
    """
    CONTAINER_NAME = "EDHREC article search"  # override

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return (("edhrec.com/articles/" in url.lower()
                 or "articles.edhrec.com/" in url.lower()) and "/search/" in url.lower())
