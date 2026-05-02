"""

    mtg.deck.scrapers.archidekt
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape Archidekt decklists.

    @author: mazz3rr

"""
import json
import logging
from datetime import datetime
from typing import override

from mtg.constants import Json
from mtg.deck.scrapers.abc import DeckScraper, DeckUrlsContainerScraper, folder_container_scraper
from mtg.lib.scrape.core import find_links, strip_url_query
from mtg.yt.discover import UrlHook

_log = logging.getLogger(__name__)
URL_PREFIX = "https://archidekt.com"


URL_HOOKS = (
    # regular deck
    UrlHook(
        ('"archidekt.com/decks/"', ),
    ),
    # snapshot deck
    UrlHook(
        ('"archidekt.com/snapshots/"', ),
    ),
    # folder
    UrlHook(
        ('"archidekt.com/folders/"', ),
    ),
    # user #1
    UrlHook(
        ('"archidekt.com/u/"', ),
    ),
    # user #2
    UrlHook(
        ("archidekt.com/user/", ),
    ),
    # user #3
    UrlHook(
        ('"archidekt.com/search/decks?"', "owner="),
    ),
    # user #4
    UrlHook(
        ('"archidekt.com/search/decks?"', "ownerusername="),
    ),
)


@DeckScraper.registered
class ArchidektDeckScraper(DeckScraper):
    """Scraper of Archidekt decklist page.
    """
    JSON_FROM_SOUP = True  # override
    EXAMPLE_URLS = (
        "https://archidekt.com/decks/13183782/isperia_modified",
        "https://archidekt.com/decks/22232192/azurious_auras",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return "archidekt.com/decks/" in url.lower()

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = super().normalize_url(url)
        return strip_url_query(url).replace("/image/", "/")

    # FIXME: Archidekt servers seem to respond with a real 404 response (at least at times)
    @override
    def _is_soft_404_error(self) -> bool:
        tag = self._soup.find("h1")
        return tag and tag.text.strip() == "Page not found"

    @override
    def _get_json_from_soup(self) -> Json:
        json_data = json.loads(self._soup.find("script", id="__NEXT_DATA__").text)
        return json_data["props"]["pageProps"]["redux"]["deck"]

    @override
    def _parse_input_for_metadata(self) -> None:
        fmt_tag = self._soup.find("div", class_=lambda c: c and "deckHeader_format" in c)
        if fmt_tag:
            fmt_text = fmt_tag.text
            suffix = fmt_tag.find("div").text if fmt_tag.find("div") else ""
            fmt = fmt_text.removesuffix(suffix).strip().lower()
            if "/" in fmt:
                fmt, *_ = fmt.split("/")
            self._update_fmt(fmt.strip())
        self._metadata["name"] = self._json["name"]
        self._metadata["author"] = self._json["owner"]
        self._metadata["views"] = self._json["viewCount"]
        date_text = self._json["updatedAt"].replace("Z", "+00:00")
        self._metadata["date"] = datetime.fromisoformat(date_text).date()
        if edh_bracket := self._json.get("edhBracket"):
            self._metadata["edh_bracket"] = edh_bracket
        if tags := self._json.get("deckTags"):
            self._metadata["tags"] = self.normalize_metadata_deck_tags(tags)

    def _parse_card_json(self, card_json: Json) -> None:
        # filter tokens (#449)
        if card_json.get("setType") == "token":
            return
        if types := card_json.get("types"):
            if "Token" in types:
                return
        if card_json.get("layout") == "token":
            return
        name = card_json["name"]
        quantity = card_json["qty"]
        set_code = card_json["setCode"]
        collector_number = card_json["collectorNumber"]
        categories = card_json["categories"]
        card = self.find_card(name, (set_code, collector_number))
        playset = self.get_playset(card, quantity)
        if "Commander" in categories:
            self._set_commander(card)
        elif "Sideboard" in categories:
            self._sideboard.extend(playset)
        else:
            self._maindeck.extend(playset)

    @override
    def _parse_input_for_decklist(self) -> None:
        for v in self._json["cardMap"].values():
            self._parse_card_json(v)


@DeckScraper.registered
class ArchidektSnapshotScraper(ArchidektDeckScraper):
    """Scraper of Archidekt snapshot decklist page.
    """
    EXAMPLE_URLS = (
        "https://archidekt.com/snapshots/47613",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return "archidekt.com/snapshots/" in url.lower()


@folder_container_scraper
@DeckUrlsContainerScraper.registered
class ArchidektFolderScraper(DeckUrlsContainerScraper):
    """Scraper of Archidekt folder page.
    """
    CONTAINER_NAME = "Archidekt folder"  # override
    DECK_SCRAPER_TYPES = ArchidektDeckScraper,  # override
    EXAMPLE_URLS = (
        "https://archidekt.com/folders/42773?orderBy=name&dir=asc",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return "archidekt.com/folders/" in url.lower()

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = super().normalize_url(url)
        return strip_url_query(url)

    @override
    def _parse_input_for_decks_data(self) -> None:
        self._deck_urls = find_links(
            self._soup, css_selector='a[href*="/decks/"]', url_prefix=URL_PREFIX)


@DeckUrlsContainerScraper.registered
class ArchidektUserScraper(DeckUrlsContainerScraper):
    """Scraper of Archidekt folder page.
    """
    CONTAINER_NAME = "Archidekt user"  # override
    DECK_SCRAPER_TYPES = ArchidektDeckScraper,  # override
    DECK_URL_PREFIX = URL_PREFIX  # override
    EXAMPLE_URLS = (
        "https://archidekt.com/u/tauna",
        "https://archidekt.com/user/5879",
        "https://archidekt.com/search/decks?owner=BacaIhau&ownerexact=true",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        url = url.lower()
        return (
            "archidekt.com/u/" in url
            or "archidekt.com/user/" in url
            or (
                "archidekt.com/search/decks?" in url
                and ("owner=" in url or "ownerusername=" in url)
            )
        )

    @override
    def _parse_input_for_decks_data(self) -> None:
        info_tags = self._soup.find_all("div", class_="deckLink_info__ww_n5")
        self._deck_urls = [div.find("a")["href"] for div in info_tags]
