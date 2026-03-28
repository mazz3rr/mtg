"""

    mtg.deck.scrapers.topdeck
    ~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape TopDeck.gg decklist containers.

    @author: mazz3rr

"""
import logging
from typing import Type, override

import dateutil.parser
from bs4 import BeautifulSoup, Tag

from mtg.constants import Json
from mtg.deck.arena import ArenaParser
from mtg.deck.core import DeckParser
from mtg.deck.abc import JsonBasedDeckParser
from mtg.deck.scrapers.abc import ContainerScraper, DeckScraper, DecksJsonContainerScraper, \
    HybridContainerScraper
from mtg.lib.common import ParsingError, decode_escapes, extract_int
from mtg.lib.scrape.core import ScrapingError, fetch_json, strip_url_query

_log = logging.getLogger(__name__)


@DeckScraper.registered
class TopDeckDeckScraper(DeckScraper):
    """Scraper of TopDeck.gg decklist page.
    """
    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return "topdeck.gg/deck/" in url.lower()

    @override
    def _is_page_inaccessible(self) -> bool:
        tag = self._soup.find("h3")
        return tag and tag.text.strip() == "Unable to Display Deck"

    @override
    def _parse_metadata(self) -> None:
        header_tag = self._soup.select_one("div.row.align-items-center")
        if not header_tag:
            raise ScrapingError("Header tag not found", scraper=type(self), url=self.url)
        if author_tag := header_tag.find("h1"):
            self._metadata["author"] = author_tag.text.strip()
        if event_tag := header_tag.find("h3"):
            self._metadata.setdefault("event", {})["name"] = event_tag.text.strip()
        if event_url_tag := header_tag.find("a", href=lambda h: h and h.startswith("/bracket/")):
            self._metadata.setdefault(
                "event", {})["url"] = "https://topdeck.gg" + event_url_tag.attrs["href"]
        if fmt_tag := header_tag.find("small"):
            self._update_fmt(fmt_tag.text.strip().removeprefix("Magic: The Gathering "))
        if p_tag := header_tag.find("p"):
            rank, record = p_tag.text.strip().split("•Record: ", maxsplit=1)
            self._metadata.setdefault("event", {})["rank"] = extract_int(rank)
            self._metadata.setdefault("event", {})["record"] = record.strip()

    @staticmethod
    def sanitize_decklist(decklist: str) -> str:
        # in profile JSON there are cases of annotations (e.g. "Imported from [link]")
        # after a triple-linebreak
        if "\n\n\n" in decklist:
            decklist, *_ = decklist.split("\n\n\n")
            decklist += "\n"
        return decklist.replace("~~Commanders~~", "Commander").replace(
            "~~Mainboard~~", "Deck").replace("~~Sideboard~~", "Sideboard").replace(
            '~~Companion~~', "Companion")

    @override
    def _parse_deck(self) -> None:
        decklist_tag = self._soup.find(
            "script", string=lambda s: s and "const decklistContent = `" in s)
        if not decklist_tag:
            raise ScrapingError("Decklist tag not found", scraper=type(self), url=self.url)
        _, decklist = decklist_tag.text.split("const decklistContent = `", maxsplit=1)
        decklist, _ = decklist.split("`;", maxsplit=1)
        self._decklist = self.sanitize_decklist(decklist)


def check_unexpected_urls(urls: list[str], *scrapers: Type[DeckScraper]) -> None:
    names = [scraper.__name__ for scraper in scrapers]
    if unexpected := [url for url in urls if url.startswith("http") and
                      not any(s.is_valid_url(url) for s in scrapers)]:
        _log.warning(f"Non-{names} deck(s) found: {', '.join(unexpected)}")


class TopDeckBracketDeckJsonParser(JsonBasedDeckParser):
    """Parser of TopDeck.gg bracket deck JSON data.
    """
    @override
    def _parse_metadata(self) -> None:
        if author := self._deck_data.get("name", self._deck_data.get("username")):
            self._metadata["author"] = author
        if elo := self._deck_data.get("elo"):
            self._metadata["elo"] = elo

    @override
    def _parse_deck(self) -> None:
        decklist = self._deck_data["decklist"]
        self._decklist = TopDeckDeckScraper.sanitize_decklist(decode_escapes(decklist))


@HybridContainerScraper.registered
class TopDeckBracketScraper(HybridContainerScraper):
    """Scraper of TopDeck.gg bracket page.
    """
    CONTAINER_NAME = "TopDeck.gg bracket"  # override
    JSON_BASED_DECK_PARSER = TopDeckBracketDeckJsonParser  # override

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return "topdeck.gg/bracket/" in url.lower()

    @staticmethod
    @override
    def sanitize_url(url: str) -> str:
        return strip_url_query(url)

    @override
    def _get_data_from_api(self) -> Json:
        return fetch_json(self.url.replace("/bracket/", "/PublicPData/"))

    @override
    def _pre_parse(self) -> None:
        self._fetch_soup()
        self._validate_soup()
        self._data = self._get_data_from_api()
        self._validate_data()

    @staticmethod
    def get_title(soup: BeautifulSoup, scraper: Type[ContainerScraper], url: str) -> str:
        title_tag = soup.select_one("title")
        if not title_tag:
            raise ScrapingError("Title tag not found", scraper=scraper, url=url)
        title = title_tag.text
        if " - " in title:
            return title.split(" - ", maxsplit=1)[0]
        return title

    @override
    def _parse_metadata(self) -> None:
        self._metadata["event"] = self.get_title(self._soup, type(self), self.url)

    @staticmethod
    def _process_json(*items: Json) -> tuple[list[str], list[Json]]:
        deck_urls, decks_data = set(), []
        for item in items:
            decklist = item["decklist"]
            if decklist.startswith("http"):
                deck_urls.add(decklist)
            elif "http" in decklist:
                deck_urls.add("http" + decklist.split("http", maxsplit=1)[1])
            else:
                decks_data.append(item)
        return sorted(deck_urls), decks_data

    @override
    def _collect(self) -> tuple[list[str], list[Tag], list[Json], list[str]]:
        items = []
        for v in self._data.values():
            d = v.get("decklist")
            # at least in profile JSON data
            # 'decklist' attributes can have a boolean value of 'true'
            if isinstance(d, str) and d:
                items.append(v)
        deck_urls, decks_data = self._process_json(*items)
        if deck_urls:
            check_unexpected_urls(deck_urls, *self._get_deck_scrapers())
        return deck_urls, [], decks_data, []


class TopDeckProfileDeckJsonParser(TopDeckBracketDeckJsonParser):
    """Parser of TopDeck.gg profile deck JSON data.
    """
    def __init__(self, deck_data: Json, metadata: Json | None = None) -> None:
        super().__init__(deck_data, metadata)
        self._url: str | None = None

    @override
    def _parse_metadata(self) -> None:
        if fmt := self._deck_data.get("rawFormat"):
            self._update_fmt(fmt)
        if event_name := self._deck_data.get("name"):
            self._metadata["event"] = {"name": event_name}
            if event_date := self._deck_data.get("date"):
                self._metadata["event"]["date"] = dateutil.parser.parse(event_date).date()
            if event_record := self._deck_data.get("record"):
                self._metadata["event"]["record"] = event_record
            if event_placement := self._deck_data.get("placement"):
                self._metadata["event"]["placement"] = event_placement
            if event_place_number := self._deck_data.get("placeNumber"):
                self._metadata["event"]["place_number"] = event_place_number
            if event_size := self._deck_data.get("size"):
                self._metadata["event"]["size"] = event_size
            if event_top_cut := self._deck_data.get("topCut"):
                self._metadata["event"]["top_cut"] = event_top_cut
        if bracket_url := self._deck_data.get("bracketLink"):
            self._metadata["bracket_url"] = bracket_url

    @override
    def _get_sub_parser(self) -> DeckParser | None:
        if self._decklist:
            return ArenaParser(self._decklist, self._metadata)
        if not self._url:
            raise ParsingError("No URL for sub-scraping")
        scraper = DeckScraper.from_url(self._url, self._metadata)
        if not scraper:
            raise ParsingError(f"No suitable scraper found for sub-scraping: {self._url!r}")
        return scraper

    @override
    def _parse_deck(self) -> None:
        decklist = self._deck_data["decklist"]
        if decklist.startswith("http"):
            self._url = decklist
        elif "http" in decklist:
            self._url = "http" + decklist.split("http", maxsplit=1)[1]
        else:
            self._decklist = TopDeckDeckScraper.sanitize_decklist(decode_escapes(decklist))


@DecksJsonContainerScraper.registered
class TopDeckProfileScraper(DecksJsonContainerScraper):
    """Scraper of TopDeck.gg profile page.
    """
    CONTAINER_NAME = "TopDeck.gg profile"  # override
    JSON_BASED_DECK_PARSER = TopDeckProfileDeckJsonParser  # override

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return "topdeck.gg/profile/" in url.lower()

    @staticmethod
    @override
    def sanitize_url(url: str) -> str:
        url = strip_url_query(url)
        return url.removesuffix("/stats")

    @override
    def _pre_parse(self) -> None:
        self._fetch_soup()
        self._validate_soup()
        self._data = self._get_data_from_api()
        self._validate_data()

    @override
    def _get_data_from_api(self) -> Json:
        return fetch_json(self.url + "/stats")

    @override
    def _validate_data(self) -> None:
        super()._validate_data()
        if not self._data.get("gameFormats"):
            raise ScrapingError("No 'gameFormats' data", scraper=type(self), url=self.url)

    @override
    def _parse_metadata(self) -> None:
        self._metadata["author"] = TopDeckBracketScraper.get_title(
            self._soup, type(self), self.url)

    @override
    def _collect(self) -> list[Json]:
        decks_data = []
        for fmt, t in self._data.get("gameFormats").items():
            if "Magic: The Gathering" in fmt:
                for td in t:
                    d = td.get("decklist")
                    if isinstance(d, str) and d:
                        decks_data.append(td)
        return decks_data
