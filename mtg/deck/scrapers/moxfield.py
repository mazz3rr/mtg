"""

    mtg.deck.scrapers.moxfield
    ~~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape Moxfield decklists.

    @author: mazz3rr

"""
import logging
from datetime import datetime
from typing import override

from selenium.common import TimeoutException

from mtg.constants import Json
from mtg.deck.scrapers.abc import (
    DeckScraper, DeckUrlsContainerScraper, folder_container_scraper,
    video_throttled_deck_scraper,
)
from mtg.lib.scrape.core import (
    ScrapingError, Soft404Error, get_path_segments, normalize_url,
    strip_url_query,
)
from mtg.lib.scrape.dynamic import fetch_dynamic_soup, fetch_selenium_json
from mtg.scryfall import Card

_log = logging.getLogger(__name__)


@video_throttled_deck_scraper
@DeckScraper.registered
class MoxfieldDeckScraper(DeckScraper):
    """Scraper of Moxfield decklist page.
    """
    JSON_FROM_API = True  # override
    EXAMPLE_URLS = (
        "https://moxfield.com/decks/y98F6TIUmkmfJ0_6AOIcYw",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        url = url.lower()
        tokens = "public?q=", "/personal"  # deck search, private page
        return "moxfield.com/decks/" in url and all(t not in url for t in tokens)

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = normalize_url(url, case_sensitive=True)
        url = strip_url_query(
            url).removesuffix("/primer").removesuffix("/history").removesuffix(
            "/settings").removesuffix("/goldfish")
        return url.rstrip(".,")

    @override
    def _fetch_json(self) -> None:
        *_, decklist_id = self.url.split("/")
        if decklist_id == "undefined":
            raise Soft404Error(scraper=type(self), url=self.url)
        api_url = f"https://api2.moxfield.com/v3/decks/all/{decklist_id}"
        self._json = fetch_selenium_json(api_url)

    @override
    def _validate_json(self) -> None:
        if not self._json or not self._json.get("boards"):
            raise ScrapingError("No deck data", scraper=type(self), url=self.url)

    @override
    def _parse_input_for_metadata(self) -> None:
        fmt = self._json["format"].lower()
        self._update_fmt(fmt)
        name = self._json["name"]
        if " - " in name:
            *_, name = name.split(" - ")
        self._metadata["name"] = name
        self._metadata["likes"] = self._json["likeCount"]
        self._metadata["views"] = self._json["viewCount"]
        self._metadata["comments"] = self._json["commentCount"]
        self._metadata["author"] = self._json["createdByUser"]["displayName"]
        try:
            self._metadata["date"] = datetime.strptime(
                self._json["lastUpdatedAtUtc"], "%Y-%m-%dT%H:%M:%S.%fZ").date()
        except ValueError:  # no fractional seconds part in the date string
            self._metadata["date"] = datetime.strptime(
                self._json["lastUpdatedAtUtc"], "%Y-%m-%dT%H:%M:%SZ").date()
        if desc := self._json["description"]:
            self._metadata["description"] = desc
        if hubs := self._json.get("hubs"):
            self._metadata["hubs"] = self.normalize_metadata_deck_tags(hubs)
        if edh_bracket := self._json.get("autoBracket"):
            self._metadata["edh_bracket"] = edh_bracket

    @classmethod
    def _to_playset(cls, json_card: Json) -> list[Card]:
        scryfall_id = json_card["card"]["scryfall_id"]
        quantity = json_card["quantity"]
        name = json_card["card"]["name"]
        return cls.get_playset(cls.find_card(name, scryfall_id=scryfall_id), quantity)

    @override
    def _parse_input_for_decklist(self) -> None:
        for card in self._json["boards"]["mainboard"]["cards"].values():
            self._maindeck.extend(self._to_playset(card))
        for card in self._json["boards"]["sideboard"]["cards"].values():
            self._sideboard.extend(self._to_playset(card))
        # Oathbreaker is not fully supported by Deck objects
        if signature_spells := self._json["boards"]["signatureSpells"]:
            for card in signature_spells["cards"].values():
                self._maindeck.extend(self._to_playset(card))
        for card in self._json["boards"]["commanders"]["cards"].values():
            result = self._to_playset(card)
            self._set_commander(result[0])
        if self._json["boards"]["companions"]["cards"]:
            card = next(iter(self._json["boards"]["companions"]["cards"].items()))[1]
            result = self._to_playset(card)
            self._companion = result[0]


@folder_container_scraper
@DeckUrlsContainerScraper.registered
class MoxfieldListScraper(DeckUrlsContainerScraper):
    """Scraper of Moxfield list (formerly bookmark) page.
    """
    CONTAINER_NAME = "Moxfield list"  # override
    JSON_FROM_API = True  # override
    DECK_SCRAPER_TYPES = MoxfieldDeckScraper,  # override
    EXAMPLE_URLS = (
        "https://moxfield.com/lists/enD41-decks-i-currently-play?redirectFrom=bookmarks",
        "https://www.moxfield.com/bookmarks/enD41-decks-i-currently-play",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        if not ("moxfield.com/bookmarks/" in url.lower() or "moxfield.com/lists/" in url.lower()):
            return False
        try:
            _, bookmark_id_text = get_path_segments(url)
            return True
        except ValueError:
            return False

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = normalize_url(url, case_sensitive=True)
        return url.replace("moxfield.com/bookmarks/", "moxfield.com/lists/")

    def _get_bookmark_id(self) -> str:
        _, bookmark_id_text = get_path_segments(self.url)
        if "-" in bookmark_id_text:
            id_, *_ = bookmark_id_text.split("-")
            return id_
        return bookmark_id_text

    @override
    def _fetch_json(self) -> None:
        api_url = f"https://api2.moxfield.com/v1/bookmarks/{self._get_bookmark_id()}"
        self._json = fetch_selenium_json(api_url)

    def _validate_json(self) -> None:
        if not self._json or not self._json.get("decks") or not self._json["decks"].get("data"):
            raise ScrapingError("No decks data", scraper=type(self), url=self.url)

    @override
    def _parse_input_for_decks_data(self) -> None:
        self._deck_urls = [d["deck"]["publicUrl"] for d in self._json["decks"]["data"]]


@DeckUrlsContainerScraper.registered
class MoxfieldUserScraper(DeckUrlsContainerScraper):
    """Scraper of Moxfield user page.
    """
    CONTAINER_NAME = "Moxfield user"  # override
    JSON_FROM_API = True  # override
    DECK_SCRAPER_TYPES = MoxfieldDeckScraper,  # override
    EXAMPLE_URLS = (
        "https://moxfield.com/users/OCHiveMind",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        url = url.lower()
        if "moxfield.com/users/" not in url:
            return False
        segments = get_path_segments(url)
        try:
            _, user_name = segments
        except ValueError:
            return False
        return True

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = super().normalize_url(url)
        return strip_url_query(url)

    @override
    def _fetch_json(self) -> None:
        # 100 page size is pretty arbitrary but tested to work
        api_url_template = (
            "https://api2.moxfield.com/v2/decks/search?includePinned=true&showIllegal"
            "=true&authorUserNames={}&pageNumber=1&pageSize=100&sortType="
            "updated&sortDirection=descending&board=mainboard"
        )
        _, user_name = get_path_segments(self.url)
        self._json = fetch_selenium_json(api_url_template.format(user_name))

    def _validate_json(self) -> None:
        if not self._json or not self._json.get("data"):
            raise ScrapingError("No decks data", scraper=type(self), url=self.url)

    @override
    def _parse_input_for_decks_data(self) -> None:
        self._deck_urls = [d["publicUrl"] for d in self._json["data"]]


@DeckUrlsContainerScraper.registered
class MoxfieldDeckSearchScraper(DeckUrlsContainerScraper):
    """Scraper of Moxfield deck search results page.
    """
    SELENIUM_PARAMS = {  # override
        "xpath": "//input[@id='filter']"
    }
    CONTAINER_NAME = "Moxfield deck search"  # override
    DECK_SCRAPER_TYPES = MoxfieldDeckScraper,  # override
    EXAMPLE_URLS = (
        "https://moxfield.com/decks/public?q=eyJmaWx0ZXIiOiJwb2cyNTAxIn0%3D",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return "moxfield.com/decks/public?q=" in url.lower()

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        return normalize_url(url, case_sensitive=True)

    @override
    def _pre_parse(self) -> None:
        pass

    def _get_filter(self) -> str | None:
        try:
            soup, _, _ = fetch_dynamic_soup(self.url, self.SELENIUM_PARAMS["xpath"])
            if not soup:
                return None
        except TimeoutException:
            return None

        input_tag = soup.find("input", id="filter")
        if not input_tag:
            return None

        return input_tag.attrs["value"]

    @override
    def _parse_input_for_decks_data(self) -> None:
        filter_ = self._get_filter()
        if not filter_:
            raise ScrapingError(
                "'filter' parameter missing from API URL", scraper=type(self), url=self.url)
        # 100 page size is pretty arbitrary but tested to work
        api_url_template = (
            "https://api2.moxfield.com/v2/decks/search?pageNumber=1&pageSize=100&sort"
            "Type=updated&sortDirection=descending&filter={}"
        )
        api_url = api_url_template.format(filter_)
        json_data = fetch_selenium_json(api_url)
        if not json_data or not json_data.get("data"):
            raise ScrapingError("No deck data", scraper=type(self), url=self.url)
        self._deck_urls = [d["publicUrl"] for d in json_data["data"]]
