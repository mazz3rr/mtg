"""

    mtg.deck.scrapers.mtgvault
    ~~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape MTGVault decklists.

    @author: mazz3rr

"""
import logging
from typing import override

import dateutil.parser
from bs4 import Tag

from mtg.deck.scrapers.abc import DeckScraper, DeckUrlsContainerScraper
from mtg.scryfall import Card
from mtg.lib.scrape.core import (
    ScrapingError, fetch_soup, find_links, get_netloc_domain,
    get_path_segments, strip_url_query,
)

_log = logging.getLogger(__name__)


@DeckScraper.registered
class MtgVaultDeckScraper(DeckScraper):
    """Scraper of MTGVault decklist page.
    """
    EXAMPLE_URLS = (
        "https://www.mtgvault.com/rogib/decks/edh-for-fun-special-garruk/",
        "https://www.mtgvault.com/lemonadas/decks/athreos-shroud-veiled/",
    )

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return "mtgvault.com/" in url.lower() and "/decks/" in url.lower()

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = super().normalize_url(url)
        return strip_url_query(
            url.removesuffix("/proxy/").removesuffix("/stats/").removesuffix("/sample-hand/"))

    @override
    def _parse_input_for_metadata(self) -> None:
        if name_tag := self._soup.select_one("h1.deck-name"):
            self._metadata["name"] = name_tag.text.strip()
        if info_tag := self._soup.select_one("h3.deck-creation-info"):
            text = info_tag.text.strip().removeprefix("by ")
            if " on " in text:
                author, date = text.split(" on ", maxsplit=1)
            else:
                author, date = text, None
            self._metadata["author"] = author
            if date:
                self._metadata["date"] = dateutil.parser.parse(date).date()
        if fmt_tag := self._soup.select_one("span.tag-deck-format"):
            self._update_fmt(fmt_tag.text.strip())

    @classmethod
    def _parse_card_tag(cls, card_tag: Tag) -> list[Card]:
        qty, _ = card_tag.text.strip().split("x", maxsplit=1)
        qty = int(qty)
        name_tag = card_tag.select_one("span > a")
        name = name_tag.attrs["title"]
        return cls.get_playset(cls.find_card(name), qty)

    @override
    def _parse_input_for_decklist(self) -> None:
        maindeck_tag = self._soup.select_one("div#main-deck")
        if not maindeck_tag:
            raise ScrapingError("Main deck tag not found", scraper=type(self), url=self.url)

        for card_tag in maindeck_tag.select("div.deck-card"):
            self._maindeck += self._parse_card_tag(card_tag)

        if sideboard_tag := self._soup.select_one("div#sideboard"):
            for card_tag in sideboard_tag.select("div.deck-card"):
                self._sideboard += self._parse_card_tag(card_tag)

        if commandzone_tag := self._soup.select_one("div#command-zone"):
            for card_tag in commandzone_tag.select("div.deck-card"):
                self._set_commander(self._parse_card_tag(card_tag)[0])


@DeckUrlsContainerScraper.registered
class MtgVaultUserScraper(DeckUrlsContainerScraper):
    """Scraper of MTGVault user page.
    """
    CONTAINER_NAME = "MTGVault user"  # override
    DECK_SCRAPER_TYPES = MtgVaultDeckScraper,  # override
    DECK_URL_PREFIX = "https://www.mtgvault.com"  # override
    EXAMPLE_URLS = (
        "https://www.mtgvault.com/ahendra/",
    )

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        try:
            domain = get_netloc_domain(url, naked=True)
            segment, *_ = get_path_segments(url)
        except ValueError:
            return False
        return domain == "mtgvault.com" and segment != "decks" and "/ViewDeck.aspx" not in url

    @override
    def _parse_input_for_decks_data(self) -> None:
        deck_urls = find_links(
            self._soup, href=lambda h: h and "/decks/" in h and "/search/" not in h)

        url_template = f"{self.url}/?p=" + "{}"
        current_page = 1
        if last_tag := self._soup.find("a", string=lambda s: s and s == "Last"):
            *_, last_page = last_tag.attrs["href"].split("/?p=")
            last_page = int(last_page)
            while current_page < last_page:
                current_page += 1
                soup = fetch_soup(url_template.format(current_page))
                if soup:
                    deck_urls += find_links(
                        soup, href=lambda h: h and "/decks/" in h and "/search/" not in h)

        if not deck_urls:
            raise ScrapingError("No decks found", scraper=type(self), url=self.url)

        self._deck_urls = deck_urls
