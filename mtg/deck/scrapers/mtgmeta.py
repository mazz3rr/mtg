"""

    mtg.deck.scrapers.mtgmeta
    ~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape defunct MTGMeta.io decklists (using Wayback Machine).

    @author: mazz3rr

"""
import logging
from typing import override

import dateutil.parser
from bs4 import Tag

from mtg.constants import Json
from mtg.deck.abc import TagBasedDeckParser
from mtg.deck.scrapers.abc import DeckScraper, DeckUrlsContainerScraper, HybridContainerScraper
from mtg.lib.common import extract_float
from mtg.lib.scrape.core import ScrapingError, dissect_js, find_links, \
    strip_url_query
from mtg.lib.scrape.wayback import fetch_wayback_soup
from mtg.scryfall import Card

_log = logging.getLogger(__name__)


@DeckScraper.registered
class MtgMetaIoDeckScraper(DeckScraper):
    """Scraper of MTGMeta.io decklist page.
    """
    DATA_FROM_SOUP = True  # override

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return "mtgmeta.io/decks/" in url.lower()

    @staticmethod
    @override
    def sanitize_url(url: str) -> str:
        return strip_url_query(url)

    @override
    def _fetch_soup(self) -> None:
        self._soup = fetch_wayback_soup(self.url)

    @override
    def _validate_soup(self) -> None:
        super()._validate_soup()
        if "Error connecting to database" in str(self._soup):
            raise ScrapingError(
                "Page not available due to Internet Archive's database error", scraper=type(self),
                url=self.url)

    def _get_data_from_soup(self) -> Json:
        return dissect_js(self._soup, "const decklist = ", " ;\n  ")

    @override
    def _parse_metadata(self) -> None:
        if fmt_tag := self._soup.find(
            "a", class_="crumb", string=lambda s: s and "Home" not in s and "Decks" not in s):
            self._update_fmt(fmt_tag.text.strip())
        if name := self._data.get("dname"):
            self._metadata["name"] = name
        elif name := self._soup.select_one("h1.deckname"):
            self._metadata["name"] = name.text.strip()
        if player := self._data.get("pname"):
            self._metadata["author"] = player
        if event := self._data.get("tname"):
            self._metadata["event"] = {}
            self._metadata["event"]["name"] = event
        if place := self._data.get("place"):
            self._metadata.setdefault("event", {})["place"] = int(place)
        if info_tag := self._soup.select_one("ul#deckstats"):
            li_tags = [*info_tag.find_all("li")]
            for i, li_tag in enumerate(li_tags):
                text = li_tag.text.strip()
                if "Metashare" in text:
                    self._metadata.setdefault("meta", {})["share"] = extract_float(text)
                elif text.endswith(" Global Performance"):
                    self._metadata.setdefault("meta", {})[
                        "global_performance"] = text.removesuffix(" Global Performance")
                elif i == len(li_tags) - 1:
                    if " - " in text:
                        *_, text = text.split(" - ")
                    self._metadata["date"] = dateutil.parser.parse(text).date()

    @classmethod
    def _parse_card_json(cls, card_json: Json) -> list[Card]:
        name = card_json["card"]
        qty = int(card_json["quantity"])
        return cls.get_playset(cls.find_card(name), qty)

    @override
    def _parse_deck(self) -> None:
        for card_json in self._data["main"]:
            self._maindeck += self._parse_card_json(card_json)

        if sideboard := self._data.get("side"):
            for card_json in sideboard:
                self._sideboard += self._parse_card_json(card_json)


def _strip_wm_part(*links: str) -> list[str]:
    deck_urls = []
    for link in links:
        *_, rest = link.split("https://mtgmeta.io/")
        deck_urls.append("https://mtgmeta.io/" + rest)
    return deck_urls


@DeckUrlsContainerScraper.registered
class MtgMetaIoTournamentScraper(DeckUrlsContainerScraper):
    """Scraper of MTGMeta.io tournament page.
    """
    THROTTLING = DeckUrlsContainerScraper.THROTTLING * 10  # override
    CONTAINER_NAME = "MTGMeta.io tournament"  # override
    DECK_SCRAPERS = MtgMetaIoDeckScraper,  # override

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return "mtgmeta.io/tournaments/" in url.lower()

    @staticmethod
    @override
    def sanitize_url(url: str) -> str:
        return strip_url_query(url)

    @override
    def _fetch_soup(self) -> None:
        self._soup = fetch_wayback_soup(self.url)

    def _validate_soup(self) -> None:
        super()._validate_soup()
        if "Error connecting to database" in str(self._soup):
            raise ScrapingError(
                "Page not available due to Internet Archive's database error", scraper=type(self),
                url=self.url)

    @override
    def _collect(self) -> list[str]:
        ul_tag = self._soup.select_one("ul.playerslist")
        if not ul_tag:
            raise ScrapingError(
                "Players' list <ul> tag not found", scraper=type(self), url=self.url)
        links = find_links(ul_tag)
        return _strip_wm_part(*links)


class MtgMetaIoDeckTagParser(TagBasedDeckParser):
    """Parser of a MTGMeta.io article's decklist HTML tag.
    """
    @override
    def _parse_metadata(self) -> None:
        if info_tag := self._deck_tag.select_one("div.info-deck-block"):
            if name_tag := info_tag.select_one("div.name"):
                self._metadata["name"] = name_tag.text.strip()
            if fmt_tag := info_tag.select_one("div.format"):
                self._update_fmt(fmt_tag.text.strip())

    @override
    def _parse_deck(self) -> None:
        for card in self._deck_tag.select("div.card"):
            qty = int(card.attrs["data-qt"])
            name = card.attrs["data-name"]
            playset = self.get_playset(self.find_card(name), qty)
            if card.attrs["data-main"] == "1":
                self._maindeck += playset
            else:
                self._sideboard += playset


@HybridContainerScraper.registered
class MtgMetaIoArticleScraper(HybridContainerScraper):
    """Scraper of MTGMeta.io article page.
    """
    CONTAINER_NAME = "MTGMeta.io article"  # override
    THROTTLING = MtgMetaIoTournamentScraper.THROTTLING  # override
    TAG_BASED_DECK_PARSER = MtgMetaIoDeckTagParser  # override

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return "mtgmeta.io/articles/" in url.lower()

    @staticmethod
    @override
    def sanitize_url(url: str) -> str:
        return strip_url_query(url)

    def _fetch_soup(self) -> None:
        self._soup = fetch_wayback_soup(self.url)

    def _validate_soup(self) -> None:
        super()._validate_soup()
        if "Error connecting to database" in str(self._soup):
            raise ScrapingError(
                "Page not available due to Internet Archive's database error", scraper=type(self),
                url=self.url)

    @override
    def _parse_metadata(self) -> None:
        if title_tag := self._soup.select_one("h1.entry-title"):
            self._metadata["title"] = title_tag.text.strip()
        time_tag = self._soup.select_one("time.published") or self._soup.select_one("time.updated")
        if time_tag:
            date_text = time_tag.attrs["datetime"][:10]
            self._metadata["date"] = dateutil.parser.parse(date_text).date()
        if author_tag := self._soup.select_one("span.author-name"):
            self._metadata["author"] = author_tag.text.strip()

    @override
    def _collect(self) -> tuple[list[str], list[Tag], list[Json], list[str]]:
        article_tag = self._soup.find("article")
        if not article_tag:
            raise ScrapingError("Article tag not found", scraper=type(self), url=self.url)
        deck_tags = [*article_tag.find_all("div", class_="decklist-container")]
        p_tags = [t for t in article_tag.find_all("p") if not t.find("div", class_="deck_list")]
        deck_urls, container_urls = self._find_links_in_tags(*p_tags)
        return _strip_wm_part(*deck_urls), deck_tags, [], container_urls
