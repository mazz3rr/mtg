"""

    mtg.deck.scrapers.mtgjson
    ~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape MTGJSON decklists.

    @author: mazz3rr

"""
import json
import logging
from collections.abc import Iterator
from datetime import datetime
from typing import Literal, override

from tqdm import tqdm

from mtg.constants import DECKS_DIR, Json, PathLike
from mtg.deck.export import Exporter, FORMATS as EXPORT_FORMATS
from mtg.deck.core import Deck
from mtg.deck.scrapers.abc import DeckScraper
from mtg.lib.common import get_timestamp, logging_disabled, timed
from mtg.lib.files import getdir
from mtg.lib.scrape.core import ScrapingError, fetch_json, fetch_soup
from mtg.scryfall import Card

_log = logging.getLogger(__name__)
URL = "https://mtgjson.com/api/v5/decks/"


@DeckScraper.registered
class MtgJsonDeckScraper(DeckScraper):
    """Scraper of MTGJSON decks page.
    """
    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return "mtgjson.com/api/v5/decks/" in url.lower() and url.lower().endswith(".json")

    @override
    def _pre_parse(self) -> None:
        json_data = fetch_json(self.url)
        if not json_data or not json_data.get("data"):
            raise ScrapingError("No deck data", scraper=type(self), url=self.url)
        self._metadata["date"] = datetime.fromisoformat(json_data["meta"]["date"]).date()
        self._metadata["version"] = json_data["meta"]["version"]
        self._data = json_data["data"]

    @override
    def _parse_metadata(self) -> None:
        self._metadata["name"] = self._data["name"]
        self._metadata["release_date"] = datetime.fromisoformat(
            self._data["releaseDate"]).date()
        self._metadata["type"] = self._data["type"]

    def _parse_card_json(self, card_json: Json) -> list[Card]:
        qty = card_json["count"]
        name = card_json["name"]
        setcode = card_json["setCode"].lower()
        collector_number = card_json["number"]
        scryfall_id = card_json["identifiers"]["scryfallId"]
        card = self.find_card(
            name, set_and_collector_number=(setcode, collector_number), scryfall_id=scryfall_id)
        return self.get_playset(card, qty)

    @override
    def _parse_deck(self) -> None:
        for card_json in self._data["commander"]:
            for card in self._parse_card_json(card_json):
                self._set_commander(card)
        for card_json in self._data["mainBoard"]:
            self._maindeck += self._parse_card_json(card_json)
        for card_json in self._data["sideBoard"]:
            self._sideboard += self._parse_card_json(card_json)


def _scrape_links() -> list[str]:
    soup = fetch_soup(URL)
    if not soup:
        raise ScrapingError("No API page soup", scraper=MtgJsonDeckScraper)
    tbody = soup.find("tbody")
    link_tags = [t.find("a") for t in tbody.find_all("td", class_="link")]
    link_tags = [t for t in link_tags if t is not None]
    return [
        f"{URL}{t['href']}" for t in link_tags
        if MtgJsonDeckScraper.is_valid_url(f"{URL}{t['href']}")]


class Scraper:
    """Scrape and export all available (Constructed-viable) MTGJSON decklists.
    """
    CACHE = DECKS_DIR / "mtgjson" / "mtgjson.json"

    def __init__(
            self, dump_fmt: Literal["arena", "forge", "json", "xmage"] = "forge",
            only_new=True) -> None:
        if dump_fmt not in EXPORT_FORMATS:
            raise ValueError(f"Invalid dump format: {dump_fmt!r}. Must be one of: {EXPORT_FORMATS}")
        self._dump_fmt = dump_fmt
        self._already_scraped = self._get_already_scraped() if only_new else set()
        self._links = [l for l in _scrape_links() if l not in self._already_scraped]
        self._scraped = []

    def _get_already_scraped(self) -> set[str]:
        if self.CACHE.is_file():
            data: Json = json.loads(self.CACHE.read_text(encoding="utf-8"))
            return set(data.get(self._dump_fmt, set()))
        return set()

    @timed("scraping MTGJSON API deck page")
    def scrape(self, *mtgjson_deck_links: str) -> Iterator[Deck | None]:
        """Scrape MTGJSON API deck page for decks yielding one at a time.

        Decks not deemed Constructed-valid ones are ignored.
        """
        links = mtgjson_deck_links or self._links
        with self.CACHE.open("w", encoding="utf-8") as f:
            for i, link in enumerate(links, start=1):
                _log.info(f"Scraping deck {i}/{len(links)}: {link!r}...")
                try:
                    deck = MtgJsonDeckScraper(link).scrape(throttled=True)
                except Exception as err:
                    _log.warning(f"Scraping deck {i}/{len(links)}: {link!r} failed with: {err!r}")
                    yield None
                self._scraped.append(link)
                yield deck
            data = {self._dump_fmt: sorted({*self._scraped, *self._already_scraped})}
            json.dump(data, f, indent=4)

    def dump(self, dstdir: PathLike = "") -> None:
        """Export all Constructed decks available in MTGJSON API decks page to ```dstdir``` in the
        format provided.
        """
        timestamp = get_timestamp(filename=True)
        dstdir = dstdir or DECKS_DIR / "mtgjson" / timestamp
        dstdir = getdir(dstdir)
        with logging_disabled():
            for deck in tqdm(
                    self.scrape(), total=len(self._links), desc="Exporting MTGJSON decks..."):
                if deck:
                    exporter = Exporter(deck)
                    try:
                        match self._dump_fmt:
                            case "arena":
                                exporter.to_arena(dstdir)
                            case "forge":
                                exporter.to_forge(dstdir)
                            case "json":
                                exporter.to_json(dstdir)
                            case "xmage":
                                exporter.to_xmage(dstdir)
                    except OSError as err:
                        if "File name too long" in str(err):
                            pass
                        else:
                            raise
        _log.info(f"Scraped total number of {len(self._scraped)} deck(s)")


def dump(
        dstdir: PathLike = "",
        fmt: Literal["arena", "forge", "json", "xmage"] = "forge", only_new=True) -> None:
    """Export all Constructed decks available in MTGJSON API decks page to ```dstdir``` in the
    format provided.
    """
    Scraper(fmt, only_new).dump(dstdir)
