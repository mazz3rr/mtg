"""

    mtg.deck.scrapers.cardboardlive
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape CardBoard Live decklists.

    @author: mazz3rr

"""
import logging
from typing import override

from mtg.deck.scrapers.abc import DeckScraper
from mtg.lib.scrape.core import strip_url_query
from mtg.lib.scrape.dynamic import Xpath
from mtg.yt.discover import UrlHook

_log = logging.getLogger(__name__)
CLIPBOARD_XPATH = "//span[text()='Export to Arena']"


URL_HOOKS = (
    # regular deck
    UrlHook(
        ('"app.cardboard.live/shared-deck/"', ),
    ),
)


@DeckScraper.registered
class CardBoardLiveDeckScraper(DeckScraper):
    """Scraper of a CardBoard Live decklist page.
    """
    SELENIUM_PARAMS = {  # override
        "xpaths": [
            Xpath(CLIPBOARD_XPATH),
        ],
        "clipboard_xpath": CLIPBOARD_XPATH
    }
    EXAMPLE_URLS = (
        "https://app.cardboard.live/shared-deck/e75ebecd-6bec-11ec-8b34-12f15ef2af51?channel_id=38054155",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return "app.cardboard.live/shared-deck/" in url.lower()

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = super().normalize_url(url)
        return strip_url_query(url)

    @override
    def _parse_input_for_metadata(self) -> None:
        self._metadata["name"] = self._soup.find("h3", class_="shared-deck__title").text.strip()
        for tag in self._soup.find_all("p", class_="shared-deck__describe"):
            if "Played by: " in tag.text:
                self._metadata["author"] = tag.text.strip().removeprefix("Played by: ")
            elif "Format: " in tag.text:
                self._update_fmt(tag.text.strip().removeprefix("Format: "))
            elif "Tournament: " in tag.text:
                self._metadata["event"] = tag.text.strip().removeprefix("Tournament: ")

    @override
    def _parse_input_for_decklist(self) -> None:
        self._decklist = self._clipboard
