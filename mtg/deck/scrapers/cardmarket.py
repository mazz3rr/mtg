"""

    mtg.deck.scrapers.cardmarket
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape Cardmarket decklists.

    @author: mazz3rr

"""
import logging
from typing import override

import dateutil.parser
from bs4 import Tag

from mtg.constants import Json
from mtg.deck.abc import TagBasedDeckParser
from mtg.deck.scrapers.abc import HybridContainerScraper
from mtg.lib.common import from_iterable
from mtg.lib.scrape.core import ScrapingError, strip_url_query
from mtg.scryfall import COMMANDER_FORMATS, Card, all_formats
from mtg.yt.discover import UrlHook

_log = logging.getLogger(__name__)


URL_HOOKS = (
    # article
    UrlHook(
        ('"cardmarket.com/"', '"/Insight/Articles/"'),
        ('-"/yugioh/"', ),
    ),
    # writer
    UrlHook(
        ('"cardmarket.com/"', '"/Insight/Writers/"'),
    ),
)


class CardmarketDeckTagParser(TagBasedDeckParser):
    """Parser of Cardmarket decklist HTML tag.
    """
    @override
    def _parse_metadata(self) -> None:
        title = self._deck_tag.select_one("thead").text.strip()

        tokens = [t.lower() for t in title.split()]
        if "duel" in tokens and "commander" in tokens:
            self._update_fmt("duel")
        elif "pauper" in tokens and "commander" in tokens:
            self._update_fmt("paupercommander")
        elif "historic" in tokens and "brawl" in tokens:
            self._update_fmt("brawl")
        elif "standard" in tokens and "brawl" in tokens:
            self._update_fmt("standardbrawl")
        elif fmt := from_iterable(tokens, lambda t: t in all_formats()):
            self._update_fmt(fmt)

        if ", " in title:
            name_part, *other_parts = title.split(", ")
            self._metadata["details"] = [*other_parts]
            if " by " in name_part:
                name, author = name_part.split(" by ", maxsplit=1)
            elif " – " in name_part:
                name, author = name_part.split(" – ", maxsplit=1)
            else:
                name = name_part
                author = None
            if author:
                self._metadata["author"] = author
            self._metadata["name"] = name
        else:
            self._metadata["name"] = title if title.lower() not in (
                "main", "mainboard", "main board", "main deck", "maindeck", "decklist",
                "deck") else self._metadata["title"]

        if not self.fmt:
            if fmt := from_iterable(self._metadata["article_tags"], lambda t: t in all_formats()):
                self._update_fmt(fmt)

    @classmethod
    def _parse_li_tag(cls, li_tag: Tag) -> list[Card]:
        if len([*li_tag.select("hoverable-card")]) > 1:
            return []
        try:
            qty, *_ = li_tag.text.strip().split()
            qty = int(qty.strip())
        except ValueError:
            return []
        name = li_tag.find("hoverable-card").attrs["name"]
        return cls.get_playset(cls.find_card(name), qty)

    def _parse_ul_tags(self, ul_tags: list[Tag], has_sideboard: bool) -> None:
        for i, ul_tag in enumerate(ul_tags):
            board = self._sideboard if has_sideboard and i == len(ul_tags) - 1 else self._maindeck
            for li_tag in [t for t in ul_tag.select("li") if t.find("hoverable-card")]:
                board += self._parse_li_tag(li_tag)

    @classmethod
    def _parse_card_tag(cls, card_tag: Tag) -> list[Card]:
        name = card_tag.attrs["name"]
        qty = card_tag.previous.text.strip()
        try:
            qty = int(qty)
        except ValueError:
            return []
        return cls.get_playset(cls.find_card(name), qty)

    def _parse_table_tags(self, has_sideboard: bool) -> None:
        if has_sideboard:
            maindeck_table, sideboards_table = self._deck_tag.find_all("table")
        else:
            maindeck_table = self._deck_tag.find("table")
            sideboards_table = None
        for card_tag in maindeck_table.find_all("hoverable-card"):
            self._maindeck += self._parse_card_tag(card_tag)
        if has_sideboard:
            for card_tag in sideboards_table.find_all("hoverable-card"):
                self._sideboard += self._parse_card_tag(card_tag)

    @override
    def _parse_deck(self) -> None:
        has_sideboard = len([*self._deck_tag.find_all("thead")]) == 2
        ul_tags = [*self._deck_tag.select("ul")]
        if ul_tags:
            self._parse_ul_tags(ul_tags, has_sideboard)
        else:
            self._parse_table_tags(has_sideboard)
        has_commander = False
        if self.fmt and self.fmt in COMMANDER_FORMATS:
            has_commander = True
        if not self.fmt and len(self._maindeck) in (99, 100):
            has_commander = True
            self._update_fmt("commander")
        if has_commander and has_sideboard:
            self._derive_commander_from_sideboard()
        elif has_commander:
            self._set_commander(self._maindeck[0])
            del self._maindeck[0]


@HybridContainerScraper.registered
class CardmarketArticleScraper(HybridContainerScraper):
    """Scraper of Cardmarket article page.
    """
    SELENIUM_PARAMS = {  # override
        "xpath": "//div[@class='table-responsive mb-4']",
        "wait_for_all": True
    }
    CONTAINER_NAME = "Cardmarket article"  # override
    TAG_BASED_DECK_PARSER = CardmarketDeckTagParser  # override

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return ("cardmarket.com/" in url.lower() and "/insight/articles/" in url.lower() and
                "/yugioh/" not in url.lower())

    @staticmethod
    @override
    def sanitize_url(url: str) -> str:
        return strip_url_query(url)

    @override
    def _validate_soup(self) -> None:
        super()._validate_soup()
        cat_tag = self._soup.select_one("div.u-article-meta__category")
        if not cat_tag or cat_tag.text.strip().lower() != "magic":
            raise ScrapingError("Not a MtG article", scraper=type(self), url=self.url)

    @override
    def _parse_metadata(self) -> None:
        if author_tag := self._soup.select_one("div.u-article-meta__writer"):
            self._metadata["author"] = author_tag.text.strip()
        if date_tag := self._soup.select_one("div.u-article-meta__published"):
            self._metadata["date"] = dateutil.parser.parse(date_tag.text.strip()).date()
        if article_tags := [
            t.text.strip().lower() for t in self._soup.select("a.u-article-meta__tag")]:
            self._metadata["article_tags"] = article_tags
        if title_tag := self._soup.select_one("h1.u-content__title"):
            self._metadata["title"] = title_tag.text.strip()

    @override
    def _collect(self) -> tuple[list[str], list[Tag], list[Json], list[str]]:
        deck_tags = [
            t for t in self._soup.select("div.table-responsive.mb-4")
            if t.find("hoverable-card")]
        article_tag = self._soup.find("article")
        if not article_tag:
            err = ScrapingError("Article tag not found", scraper=type(self), url=self.url)
            _log.warning(f"Scraping failed with: {err!r}")
            return [], deck_tags, [], []
        deck_urls, container_urls = self._find_links_in_tags(*article_tag.find_all("p"))
        return deck_urls, deck_tags, [], container_urls


@HybridContainerScraper.registered
class CardmarketWriterScraper(HybridContainerScraper):
    """Scraper of Cardmarket writer page.
    """
    SELENIUM_PARAMS = {  # override
        "xpath": "//article//a[contains(@href, '/Insight/Articles/')]",
        "wait_for_all": True
    }
    CONTAINER_NAME = "Cardmarket writer"  # override
    CONTAINER_SCRAPERS = CardmarketArticleScraper,  # override
    CONTAINER_URL_PREFIX = "https://www.cardmarket.com"

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return "cardmarket.com/" in url.lower() and "/insight/writers/" in url.lower()

    @override
    def _collect(self) -> tuple[list[str], list[Tag], list[Json], list[str]]:
        article_tag = self._soup.find("article")
        if not article_tag:
            raise ScrapingError("Article tag not found", type(self), self.url)
        a_tags = article_tag.find_all("a")
        return [], [], [], [
            t["href"] for t in a_tags if t.has_attr("href") and "/Insight/Articles/" in t["href"]]
