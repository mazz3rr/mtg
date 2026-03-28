"""

    mtg.deck.scrapers.mtgo
    ~~~~~~~~~~~~~~~~~~~~~~
    Scrape MTGO decklists.

    @author: mazz3rr

"""
import logging
from typing import Type, override

import backoff
import dateutil.parser
from bs4 import BeautifulSoup

from mtg.constants import Json
from mtg.constants import SECRETS
from mtg.deck.abc import JsonBasedDeckParser
from mtg.deck.scrapers.abc import DecksJsonContainerScraper
from mtg import DeckScraper
from mtg.scryfall import all_formats
from mtg.lib.common import from_iterable, get_ordinal_suffix
from mtg.lib.scrape.core import ScrapingError, dissect_js, strip_url_query

_log = logging.getLogger(__name__)
HEADERS = {
    "Host": "www.mtgo.com",
    "User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "DNT": "1",
    "Sec-GPC": "1",
    "Connection": "keep-alive",
    "Cookie": SECRETS["mtgo"]["cookie"],
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "cross-site",
    "Priority": "u=0, i",
}


@backoff.on_exception(backoff.expo, ScrapingError, max_time=90)
def _get_json(
        soup: BeautifulSoup,
        scraper: Type[DeckScraper] | Type[DecksJsonContainerScraper], url: str) -> Json:
    data = dissect_js(
        soup, "window.MTGO.decklists.data = ", "window.MTGO.decklists.type",
        lambda s: s.rstrip().rstrip(";"))
    if data is None:
        raise ScrapingError("Nothing extracted from JavaScript", scraper=scraper, url=url)
    if not "decklists" in data:
        raise ScrapingError("No decklists data", scraper=scraper, url=url)
    return data


def _process_ranks(rank_data: Json, *decks_data: Json) -> None:
    for deck_data in decks_data:
        deck_rank_data = from_iterable(
                    rank_data, lambda d: d["loginid"] == deck_data["loginid"])
        if deck_rank_data:
            deck_data["final_rank"] = deck_rank_data["rank"]


_FORMATS = {
    "cstandard": "standard",
    "cmodern": "modern",
    "cpioneer": "pioneer",
    "cvintage": "vintage",
    "clegacy": "legacy",
    "cpauper": "pauper",
}


def _get_event_metadata(json_data: Json) -> Json:
    metadata = {"event": {}}
    if name := json_data.get("description"):
        metadata["event"]["name"] = name
    elif name := json_data.get("name"):
        metadata["event"]["name"] = name
    if type_ := json_data.get("type"):
        metadata["event"]["type"] = type_.lower()
    if player_count := json_data.get("player_count", {}).get("players"):
        metadata["event"]["player_count"] = int(player_count)
    date = json_data.get("starttime") or json_data.get("publish_date")
    if date:
        metadata["event"]["date"] = dateutil.parser.parse(date).date()
    fmt = json_data.get("format")
    if fmt:
        fmt = _FORMATS.get(fmt.lower())
        if not fmt:
            if site_name := json_data.get("site_name"):
                fmt = from_iterable(site_name.split("-"), lambda t: t in all_formats())
        if fmt:
            metadata["event"]["format"] = fmt
    return metadata


class MtgoDeckJsonParser(JsonBasedDeckParser):
    """Parser of MGTO individual decklist JSON data.
    """
    def _derive_name(self) -> str:
        name = self._deck_data["player"]
        if wins := self._deck_data.get("wins"):
            name += f" ({wins['wins']}-{wins['losses']})"
        elif rank := self._deck_data.get("final_rank"):
            rank = int(rank)
            name += f" ({rank}{get_ordinal_suffix(rank)} place)"
        return name

    @override
    def _parse_metadata(self) -> None:
        self._metadata["author"] = self._deck_data["player"]
        self._metadata["name"] = self._derive_name()
        if fmt := self._metadata.get("event", {}).get("format"):
            self._update_fmt(fmt)

    def _parse_card(self, card: Json) -> None:
        decklist = self._sideboard if card["sideboard"] == "true" else self._maindeck
        qty = int(card["qty"])
        name = card["card_attributes"]["card_name"]
        mtgo_id = int(card["card_attributes"]["digitalobjectcatalogid"])
        card = self.find_card(name, mtgo_id=mtgo_id)
        decklist += self.get_playset(card, qty)

    @override
    def _parse_deck(self) -> None:
        for card in [*self._deck_data["main_deck"], *self._deck_data.get("sideboard_deck", [])]:
            self._parse_card(card)
        self._derive_commander_from_sideboard()


@DeckScraper.registered
class MtgoDeckScraper(DeckScraper):
    """Scraper of MGTO event page that points to an individual deck.
    """
    DATA_FROM_SOUP = True  # override

    def __init__(self, url: str, metadata: Json | None = None) -> None:
        super().__init__(url, metadata)
        self._player_name = self._parse_player_name()

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return f"mtgo.com/decklist/" in url.lower() and "#deck_" in url.lower()

    @staticmethod
    @override
    def sanitize_url(url: str) -> str:
        return strip_url_query(url, keep_fragment=True)

    def _parse_player_name(self) -> str:
        *_, rest = self.url.split("/")
        _, rest = rest.split("#")
        return rest.removeprefix("deck_")

    @override
    def _get_data_from_soup(self) -> Json:
        json_data = _get_json(self._soup, type(self), self.url)
        decks_data = json_data["decklists"]
        deck_data = from_iterable(decks_data, lambda d: d["player"] == self._player_name)
        if not deck_data:
            raise ScrapingError(
                f"Deck designated by {self._player_name!r} not found", scraper=type(self),
                url=self.url)
        if rank_data := json_data.get("final_rank"):
            _process_ranks(rank_data, deck_data)
        self._metadata.update(_get_event_metadata(json_data))
        return decks_data

    @override
    def _get_sub_parser(self) -> MtgoDeckJsonParser:
        return MtgoDeckJsonParser(self._data, self._metadata)

    @override
    def _parse_metadata(self) -> None:
        pass

    @override
    def _parse_deck(self) -> None:
        pass


@DecksJsonContainerScraper.registered
class MtgoEventScraper(DecksJsonContainerScraper):
    """Scraper of MTGO event page.
    """
    CONTAINER_NAME = "MTGO event"  # override
    HEADERS = HEADERS  # override
    JSON_BASED_DECK_PARSER = MtgoDeckJsonParser  # override

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return f"mtgo.com/decklist/" in url.lower() and "#deck_" not in url.lower()

    @staticmethod
    @override
    def sanitize_url(url: str) -> str:
        return strip_url_query(url)

    @override
    def _collect(self) -> list[Json]:
        json_data = _get_json(self._soup, type(self), self.url)
        decks_data = json_data["decklists"]
        if rank_data := json_data.get("final_rank"):
            _process_ranks(rank_data, *decks_data)
        self._metadata.update(_get_event_metadata(json_data))
        return decks_data
