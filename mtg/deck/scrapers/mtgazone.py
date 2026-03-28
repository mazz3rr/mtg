"""

    mtg.deck.scrapers.mtgazone
    ~~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape MTG Arena Zone decklists.

    @author: mazz3rr

"""
import contextlib
import logging
from datetime import datetime
from typing import override

from bs4 import Tag

from mtg.constants import Json
from mtg.deck.abc import TagBasedDeckParser
from mtg.deck.core import Deck, Mode
from mtg.deck.scrapers.abc import DeckScraper, HybridContainerScraper
from mtg.lib.common import ParsingError, extract_int, from_iterable, timed
from mtg.lib.scrape.core import ScrapingError, fetch_soup, is_more_than_root_path, strip_url_query
from mtg.scryfall import ARENA_FORMATS, Card

_log = logging.getLogger(__name__)


class MtgaZoneDeckTagParser(TagBasedDeckParser):
    """Parser of an MTG Arena Zone decklist HTML tag.
    """
    @override
    def _parse_metadata(self) -> None:
        name_author_tag = self._deck_tag.find("div", class_="name-container")
        if not name_author_tag:
            raise ParsingError(
                "Name tag not found. The deck you're trying to scrape has been most probably "
                "paywalled by MTGAZone")
        name_tag = name_author_tag.find("div", class_="name")
        name, author, event = name_tag.text.strip(), None, None
        if " by " in name:
            name, author = name.split(" by ")
        elif " – " in name:
            name, event = name.split(" – ")
        self._metadata["name"] = name
        author_tag = name_author_tag.find("div", class_="by")
        if not author_tag:
            raise ParsingError(
                "Author tag not found. The deck you're trying to scrape has been most "
                "probably paywalled by MTGAZone")
        author = author_tag.text.strip().removeprefix("by ")
        self._metadata["author"] = author
        if event:
            self._metadata["event"] = event
        fmt_tag = self._deck_tag.find("div", class_="format")
        if not fmt_tag:
            raise ParsingError(
                "Format tag not found. The deck you're trying to scrape has been most probably "
                "paywalled by MTGAZone")
        fmt = fmt_tag.text.strip().lower()
        self._update_fmt(fmt)
        if time_tag := self._deck_tag.find("time", class_="ct-meta-element-date"):
            self._metadata["date"] = datetime.fromisoformat(time_tag.attrs["datetime"]).date()

    @classmethod
    def _to_playset(cls, card_tag) -> list[Card]:
        quantity = int(card_tag.attrs["data-quantity"])
        a_tag = card_tag.find("a")
        name = a_tag.text.strip()
        *_, scryfall_id = a_tag.attrs["data-cimg"].split("/")
        scryfall_id, *_ = scryfall_id.split(".jpg")
        return cls.get_playset(cls.find_card(name, scryfall_id=scryfall_id), quantity)

    def _process_decklist_tag(self, decklist_tag: Tag) -> list[Card]:
        decklist = []
        card_tags = decklist_tag.find_all("div", class_="card")
        for card_tag in card_tags:
            decklist.extend(self._to_playset(card_tag))
        return decklist

    @override
    def _parse_deck(self) -> None:
        if commander_tag := self._deck_tag.select_one("div.decklist.short.commander"):
            for card in self._process_decklist_tag(commander_tag):
                self._set_commander(card)

        if companion_tag := self._deck_tag.select_one("div.decklist.short.companion"):
            self._companion = self._process_decklist_tag(companion_tag)[0]

        main_tag = self._deck_tag.select_one("div.decklist.main")
        self._maindeck = self._process_decklist_tag(main_tag)

        if sideboard_tags := self._deck_tag.select("div.decklist.sideboard"):
            with contextlib.suppress(IndexError):
                sideboard_tag = sideboard_tags[1]
                self._sideboard = self._process_decklist_tag(sideboard_tag)


@DeckScraper.registered
class MtgaZoneDeckScraper(DeckScraper):
    """Scraper of MTG Arena Zone decklist page.
    """
    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return "mtgazone.com/user-decks/" in url.lower() or "mtgazone.com/deck/" in url.lower()

    @staticmethod
    @override
    def sanitize_url(url: str) -> str:
        return strip_url_query(url)

    @override
    def _get_sub_parser(self) -> MtgaZoneDeckTagParser:
        deck_tag = self._soup.find("div", class_="deck-block")
        if deck_tag is None:
            raise ScrapingError(
                "Deck tag not found (page probably paywalled)", scraper=type(self), url=self.url)
        return MtgaZoneDeckTagParser(deck_tag, self._metadata)

    @override
    def _parse_metadata(self) -> None:
        pass

    @override
    def _parse_deck(self) -> None:
        pass


@HybridContainerScraper.registered
class MtgaZoneArticleScraper(HybridContainerScraper):
    """Scraper of MTG Arena Zone article page.
    """
    CONTAINER_NAME = "MTGAZone article"
    TAG_BASED_DECK_PARSER = MtgaZoneDeckTagParser  # override

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return is_more_than_root_path(url, "mtgazone.com") and not any(
            t in url.lower() for t in ("/user-decks", "/deck/", "/plans/premium",
                                       "/mtg-arena-codes", "/author/", "jump-in"))

    @staticmethod
    @override
    def sanitize_url(url: str) -> str:
        return strip_url_query(url)

    def _collect_urls(self) -> tuple[list[str], list[str]]:
        article_tag = self._soup.find("article")
        if not article_tag:
            raise ScrapingError("Article tag not found", scraper=type(self), url=self.url)

        # filter out paragraphs that are covered by tag-based deck parser
        p_tags = [
            t for t in article_tag.find_all("p") if not t.find("div", class_="deck-block")]

        return self._find_links_in_tags(*p_tags)

    @override
    def _collect(self) -> tuple[list[str], list[Tag], list[Json], list[str]]:
        deck_urls, container_urls = self._collect_urls()
        return deck_urls, [*self._soup.find_all("div", class_="deck-block")], [], container_urls


@HybridContainerScraper.registered
class MtgaZoneAuthorScraper(HybridContainerScraper):
    """Scraper of MTG Arena Zone author page.
    """
    CONTAINER_NAME = "MTGAZone author"  # override
    DECK_SCRAPERS = MtgaZoneDeckScraper,  # override
    CONTAINER_SCRAPERS = MtgaZoneArticleScraper,  # override

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return "mtgazone.com/author/" in url.lower()

    @staticmethod
    @override
    def sanitize_url(url: str) -> str:
        return strip_url_query(url)

    @override
    def _collect(self) -> tuple[list[str], list[Tag], list[Json], list[str]]:
        deck_urls, article_urls = self._find_links_in_tags(css_selector="article > h2 > a")
        return deck_urls, [], [], article_urls


def _parse_tiers(table: Tag) -> dict[str, int]:
    tiers = {}
    for row in table.find_all("tr"):
        tier_col, deck_col = row.find_all("td")
        tier = extract_int(tier_col.find("strong").text)
        deck = deck_col.find("a").text.strip()
        tiers[deck] = tier
    return tiers


def _parse_meta_deck(deck_tag: Tag, decks2tiers: dict[str, int], deck_place: int) -> Deck:
    deck = MtgaZoneDeckTagParser(deck_tag).parse(
        suppress_invalid_deck=False, suppress_card_not_found=False)
    meta = {
        "meta": {
            "place": deck_place
        }
    }
    tier = decks2tiers.get(deck.name)
    if tier is None:
        deck_name = from_iterable(decks2tiers, lambda d: deck.name in d)
        if deck_name:
            tier = decks2tiers[deck_name]
    if tier:
        meta["meta"]["tier"] = tier
    deck.update_metadata(meta=meta["meta"])
    return deck


@timed("scraping meta decks")
def scrape_meta(fmt="standard", bo3=True) -> list[Deck]:
    formats = {fmt for fmt in ARENA_FORMATS if fmt not in {"brawl", "standardbrawl"}}
    formats = sorted({*formats, "pioneer"})
    fmt = fmt.lower()
    if fmt not in formats:
        raise ValueError(f"Invalid format: {fmt!r}. Can be only one of: {formats}")

    mode = "-bo3" if bo3 else "-bo1"
    if fmt == "pioneer":
        mode = ""
    url = f"https://mtgazone.com/{fmt}{mode}-metagame-tier-list/"

    soup = fetch_soup(url)
    if not soup:
        raise ScrapingError(scraper=MtgaZoneDeckTagParser, url=url)
    time_tag = soup.find("time", class_="ct-meta-element-date")
    deck_date = datetime.fromisoformat(time_tag.attrs["datetime"]).date()
    tier_table = soup.find("figure", class_="wp-block-table")
    table_body = tier_table.find("tbody")
    decks2tiers = _parse_tiers(table_body)

    decks = []
    for i, deck_tag in enumerate(soup.find_all("div", class_="deck-block"), start=1):
        deck = _parse_meta_deck(deck_tag, decks2tiers, i)
        deck.update_metadata(date=deck_date)
        deck.update_metadata(mode=mode[1:].title() if mode else Mode.BO3.value)
        decks.append(deck)

    return decks
