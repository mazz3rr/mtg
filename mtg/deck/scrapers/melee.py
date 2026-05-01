"""

    mtg.deck.scrapers.melee
    ~~~~~~~~~~~~~~~~~~~~~~~
    Scrape Melee.gg decklists.

    @author: mazz3rr

"""
import logging
from typing import override

import dateutil.parser

from mtg.constants import SECRETS
from mtg.deck.scrapers.abc import DeckScraper, DeckUrlsContainerScraper
from mtg.lib.common import from_iterable
from mtg.lib.scrape.core import ScrapingError, find_links

_log = logging.getLogger(__name__)


HEADERS = {
    "Host": "melee.gg",
    "User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,"
              "image/png,image/svg+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Connection": "keep-alive",
    "Cookie": SECRETS["melee_gg"]["cookie"],
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Priority": "u=0, i",
    "TE": "trailers",
}
ALT_DOMAIN = "mtgmelee.com"
URL_PREFIX = "https://melee.gg"


@DeckScraper.registered
class MeleeGgDeckScraper(DeckScraper):
    """Scraper of Melee.gg decklist page.
    """
    HEADERS = HEADERS
    EXAMPLE_URLS = (
        "https://melee.gg/Decklist/View/7776"
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return "melee.gg/decklist/" in url.lower() or f"{ALT_DOMAIN}/decklist/" in url.lower()

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = super().normalize_url(url)
        return url.replace(ALT_DOMAIN, "melee.gg")

    @override
    def _is_page_inaccessible(self) -> bool:
        tag = self._soup.select_one("div.content.page-with-container")
        return tag and "You are not authorized to access this Decklist" in tag.text

    @override
    def _parse_input_for_metadata(self) -> None:
        self._metadata["name"] = self._soup.select_one("div.decklist-title").text.strip()
        if author_tag := self._soup.find(
                "a", class_=lambda c: c and "text-nowrap" in c and "text-muted" in c,
                href=lambda h: h and h.startswith("/Profile/Index/")):
            self._metadata["author"] = author_tag.attrs["href"].removeprefix("/Profile/Index/")
        if event_tag := self._soup.find(
                "a", class_=lambda c: c and "text-nowrap" in c,
                href=lambda h: h and h.startswith("/Tournament/View/")):
            self._metadata["event"] = event_tag.text.strip()
        if date_tag := self._soup.find("span", {"data-toggle": "date"}):
            self._metadata["date"] = dateutil.parser.parse(date_tag.attrs["data-value"]).date()
        info_tag = self._soup.select_one("div.decklist-details-row")
        info_text = info_tag.text.strip()
        sep = "&bullet;"
        parts = [p.strip() for p in info_text.split(sep)]
        if fmt := from_iterable(
                parts, lambda p: p != '-' and all(
                    t not in p for t in ("Magic: ", "Deck: ", "Sideboard: "))):
            self._update_fmt(fmt)

    def _normalize_decklist(self) -> None:
        parts = self._decklist.split("\n\n")
        new_parts = ["", "", "", ""]
        for part in parts:
            if part.startswith("Commander"):
                new_parts[0] = part
            elif part.startswith("Companion"):
                new_parts[1] = part
            elif part.startswith("Deck"):
                new_parts[2] = part
            elif part.startswith("Sideboard"):
                new_parts[3] = part
        self._decklist = "\n\n".join([p for p in new_parts if p])

    @override
    def _parse_input_for_decklist(self) -> None:
        decklist_tag = self._soup.select_one("pre#decklist-text")
        if not decklist_tag:
            raise ScrapingError("Decklist tag not found", scraper=type(self), url=self.url)
        self._decklist = decklist_tag.text.replace(
            "\r\n", "\n").replace("MainDeck", "Deck").strip()
        self._normalize_decklist()


@DeckUrlsContainerScraper.registered
class MeleeGgTournamentScraper(DeckUrlsContainerScraper):
    """Scraper of Melee.gg tournament page.
    """
    SELENIUM_PARAMS = {  # override
        "xpath": '//a[@data-type="decklist"]'
    }
    CONTAINER_NAME = "Melee.gg tournament"  # override
    DECK_SCRAPER_TYPES = MeleeGgDeckScraper,  # override
    DECK_URL_PREFIX = URL_PREFIX  # override
    EXAMPLE_URLS = (
        "https://melee.gg/Tournament/View/115613",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return "melee.gg/tournament/" in url.lower() or f"{ALT_DOMAIN}/tournament/" in url.lower()

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        return MeleeGgDeckScraper.normalize_url(url)

    @override
    def _parse_input_for_decks_data(self) -> None:
        game_tag = self._soup.find("p", id="tournament-headline-game")
        if not game_tag.text.strip() == "Game: Magic: The Gathering":
            raise ScrapingError("Not a MtG tournament", scraper=type(self), url=self.url)
        deck_tags = self._soup.find_all("a", href=lambda h: h and "/Decklist/View/" in h)
        if not deck_tags:
            raise ScrapingError("Deck tags not found", scraper=type(self), url=self.url)
        self._deck_urls = [deck_tag.attrs["href"] for deck_tag in deck_tags]


@DeckUrlsContainerScraper.registered
class MeleeGgProfileScraper(DeckUrlsContainerScraper):
    """Scraper of Melee.gg profile page.
    """
    SELENIUM_PARAMS = {  # override
        "xpath": '//tr[@role="row"]'
    }
    CONTAINER_NAME = "Melee.gg profile"  # override
    DECK_SCRAPER_TYPES = MeleeGgDeckScraper,  # override
    DECK_URL_PREFIX = URL_PREFIX  # override
    EXAMPLE_URLS = (
        "https://melee.gg/Profile/Index/MAJH81996",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return "melee.gg/profile/" in url.lower() or f"{ALT_DOMAIN}/profile/" in url.lower()

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        return MeleeGgDeckScraper.normalize_url(url)

    @override
    def _parse_input_for_decks_data(self) -> None:
        links = find_links(self._soup)
        self._deck_urls = [l for l in links if l.startswith('/Decklist/View/')]
