"""

    mtg.deck.scrapers.tcdecks
    ~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape TC Decks decklists.

    @author: mazz3rr

"""
import logging
from datetime import datetime
from typing import override

from bs4 import NavigableString, Tag

from mtg.constants import Json
from mtg.constants import SECRETS
from mtg.deck.scrapers.abc import DeckScraper, DeckUrlsContainerScraper
from mtg.scryfall import Card
from mtg.lib.numbers import extract_int
from mtg.lib.scrape.core import ScrapingError, get_netloc_domain, get_query_data

_log = logging.getLogger(__name__)


HEADERS = {
    "Host": "www.tcdecks.net",
    "User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "DNT": "1",
    "Sec-GPC": "1",
    "Connection": "keep-alive",
    "Cookie": SECRETS["tcdecks"]["cookie"],
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Priority": "u=0, i",
}


@DeckScraper.registered
class TCDecksDeckScraper(DeckScraper):
    """Scraper of TCDecks decklist page.
    """
    HEADERS = HEADERS  # override
    EXAMPLE_URLS = (
        "https://www.tcdecks.net/deck.php?id=38058&iddeck=347793",
    )

    def __init__(self, url: str, metadata: Json | None = None) -> None:
        super().__init__(url, metadata)
        self._deck_tag: Tag | None = None

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        url = url.lower()
        query_params = get_query_data(url)
        return (
            get_netloc_domain(url, naked=True) == "tcdecks.net"
            and all(param in query_params for param in ("id", "iddeck"))
        )

    @override
    def _parse_input_for_metadata(self) -> None:
        title_tag = self._soup.select_one('div article fieldset legend')
        self._metadata["event"] = {}
        name = title_tag.find("h3").text.strip()
        if not name:
            raise ScrapingError("No deck data available")
        self._metadata["event"]["name"] = name
        fmt, players, date = title_tag.find("h5").text.strip().split(" | ")
        fmt = fmt.removeprefix("Format: ").lower()
        players = players.removeprefix("Number of Players: ")
        date = date.removeprefix("Date: ")
        self._update_fmt(fmt)
        self._metadata["event"]["players"] = extract_int(players)
        self._metadata["event"]["date"] = datetime.strptime(date, '%d/%m/%Y').date()
        self._deck_tag = self._soup.find("table", class_="table_deck")
        th_tags = [*self._deck_tag.select("tr th")][:3]
        author_theme_tag, pos_tag, name_tag = th_tags
        author, theme = author_theme_tag.text.strip().split(" playing ")
        self._metadata["author"] = author
        self._update_archetype_or_theme(theme)
        self._metadata["event"]["position"] = extract_int(pos_tag.text.strip().removeprefix(
            "Position: "))
        self._metadata["name"] = name_tag.text.strip().removeprefix("Deck Name: ")

    @classmethod
    def _parse_td_tag(cls, td_tag: Tag) -> list[Card]:
        cards = []
        qty, name = None, None
        for el in td_tag:
            if isinstance(el, NavigableString) and str(el).strip():
                qty = int(str(el).strip())
            elif el.name == "a" and el.attrs.get("class") == ["screenshot"]:
                name = el.text.strip()
            elif el.name == "br":
                cards += cls.get_playset(cls.find_card(name), qty)
                qty, name = None, None
        return cards

    @override
    def _parse_input_for_decklist(self) -> None:
        for td_tag in self._deck_tag.find_all("td", valign="top"):
            if td_tag.attrs.get("id") == "sideboard":
                self._sideboard += self._parse_td_tag(td_tag)
            else:
                self._maindeck += self._parse_td_tag(td_tag)


@DeckUrlsContainerScraper.registered
class TCDecksEventScraper(DeckUrlsContainerScraper):
    """Scraper of TCDecks event page.
    """
    CONTAINER_NAME = "TCDecks event"  # override
    HEADERS = HEADERS  # override
    DECK_SCRAPER_TYPES = TCDecksDeckScraper,  # override
    DECK_URL_PREFIX = "https://www.tcdecks.net/"  # override

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:  # override
        url = url.lower()
        query_params = get_query_data(url)
        return (
            get_netloc_domain(url, naked=True) == "tcdecks.net"
            and "id" in query_params
            and "iddeck" not in query_params
        )

    @override
    def _parse_input_for_decks_data(self) -> None:  # override
        table_tag = self._soup.select_one('table[class*="tourney"]')
        if not table_tag:
            raise ScrapingError("Event table tag not found", scraper=type(self), url=self.url)
        a_tags = table_tag.find_all("a", href=lambda h: h and "deck.php?id=" in h)
        if not a_tags:
            raise ScrapingError("Deck tags not found", scraper=type(self), url=self.url)
        self._deck_urls = sorted(set(a_tag.attrs["href"] for a_tag in a_tags))
