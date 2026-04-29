"""

    mtg.deck.scrapers.mtgtop8
    ~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape MTGTop8 decklists.

    @author: mazz3rr

"""
import contextlib
import logging
from datetime import datetime
from typing import override

from mtg.deck.scrapers.abc import DeckScraper, DeckUrlsContainerScraper
from mtg.lib.numbers import extract_int
from mtg.lib.scrape.core import ScrapingError

_log = logging.getLogger(__name__)


EVENT_RANKS = "minor", "regular", "major"  # indicated by number of stars (1, 2, 3)


# TODO: scrape metagame
@DeckScraper.registered
class MtgTop8DeckScraper(DeckScraper):
    """Scraper of MTGTop8 decklist page.
    """
    EXAMPLE_URLS = (
        "https://mtgtop8.com/event?e=1989&d=214164&f=MO",
    )

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return "mtgtop8.com/event?e=" in url.lower() and "&d=" in url.lower()

    @staticmethod
    @override
    def normalize_url(url: str) -> str:
        return url.removesuffix("/").replace("&switch=visual", "").replace(
            "&switch=text", "") + "&switch=text"

    def _validate_soup(self) -> None:
        super()._validate_soup()
        if _ := self._soup.find("div", string="No event could be found."):
            raise ScrapingError("No event could be found", scraper=type(self), url=self.url)

    @override
    def _parse_input_for_metadata(self) -> None:
        event_tag, name_tag = [tag for tag in self._soup.find_all("div", class_="event_title")
                               if tag.find("a", class_="arrow") is None]
        self._metadata["event"] = {}
        self._metadata["event"]["name"] = event_tag.text.strip().removesuffix(" Event")
        place, name = name_tag.text.strip().split(maxsplit=1)
        self._metadata["event"]["place"] = place
        if "-" in name:
            name, author = name.split("-", maxsplit=1)
            self._metadata["name"] = name.strip()
            self._metadata["author"] = author.strip()
        else:
            self._metadata["name"] = name.strip()
        fmt_tag = self._soup.find("div", class_="meta_arch")
        self._update_fmt(fmt_tag.text.strip().lower())
        with contextlib.suppress(IndexError):
            self._metadata["event"]["rank"] = EVENT_RANKS[len(fmt_tag.find_all("img")) - 1]
        players_date_text = self._soup.find('div', style='margin-bottom:5px;').text.strip()
        if "-" in players_date_text:
            players_text, date_text = players_date_text.split("-", maxsplit=1)
            self._metadata["event"]["players"] = extract_int(players_text)
        else:
            date_text = players_date_text
        self._metadata["event"]["date"] = datetime.strptime(date_text.strip(), '%d/%m/%y').date()
        if source_tag := self._soup.find("a", target="_blank"):
            self._metadata["event"]["source"] = source_tag.text.strip()

    @override
    def _parse_input_for_decklist(self) -> None:
        deck_tag = self._soup.find("div", style="display:flex;align-content:stretch;")
        cards, commander_on = self._maindeck, False
        for block_tag in deck_tag:
            for sub_tag in block_tag:
                if sub_tag.name == "div" and sub_tag.attrs.get("class") == ['O14']:
                    if sub_tag.text == "SIDEBOARD":
                        cards = self._sideboard
                        commander_on = False
                    elif sub_tag.text == "COMMANDER":
                        commander_on = True
                    else:
                        commander_on = False
                if "deck_line" in sub_tag.attrs["class"]:
                    quantity, name = sub_tag.text.split(maxsplit=1)
                    card = self.find_card(name.strip())
                    if commander_on:
                        self._set_commander(card)
                    else:
                        quantity = extract_int(quantity)
                        cards += self.get_playset(card, quantity)


@DeckUrlsContainerScraper.registered
class MtgTop8EventScraper(DeckUrlsContainerScraper):
    """Scraper of MTGTop8 event page.
    """
    CONTAINER_NAME = "MTGTop8 event"  # override
    DECK_SCRAPER_TYPES = MtgTop8DeckScraper,  # override
    DECK_URL_PREFIX = "https://www.mtgtop8.com/event"  # override
    EXAMPLE_URLS = (
        "https://mtgtop8.com/event?e=1989&",
    )

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return "mtgtop8.com/event?e=" in url.lower() and "&d=" not in url.lower()

    @staticmethod
    @override
    def normalize_url(url: str) -> str:
        return MtgTop8DeckScraper.normalize_url(url)

    @override
    def _parse_input_for_decks_data(self) -> None:
        a_tags = [tag for tag in self._soup.find_all(
            "a", href=lambda h: h and "e=" in h and "&d="in h) if not tag.find("img")
                  and tag.text not in ('Switch to Visual', '→')]
        deck_urls = {}
        for a_tag in a_tags:
            deck_urls[a_tag.text] = a_tag.attrs["href"]
        if not deck_urls:
            raise ScrapingError("No decks found", scraper=type(self), url=self.url)
        self._deck_urls = [*deck_urls.values()]
