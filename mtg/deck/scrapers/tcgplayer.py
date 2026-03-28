"""

    mtg.deck.scrapers.tcgplayer
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape TCG Player decklists.

    @author: mazz3rr

"""
import contextlib
import json
import logging
from datetime import datetime
from typing import Type, override

import dateutil.parser
from bs4 import BeautifulSoup, Tag
from httpcore import ReadTimeout
from requests import HTTPError
from selenium.common import TimeoutException

from mtg.constants import Json, SECRETS
from mtg.deck.abc import JsonBasedDeckParser
from mtg.deck.scrapers.abc import DeckScraper, DeckUrlsContainerScraper, DecksJsonContainerScraper, \
    HybridContainerScraper
from mtg.lib.common import extract_int
from mtg.lib.scrape.core import ScrapingError, fetch_json, strip_url_query, throttle
from mtg.lib.scrape.dynamic import SCROLL_DOWN_TIMES, fetch_dynamic_soup
from mtg.scryfall import Card

_log = logging.getLogger(__name__)
HEADERS = {
    "Host": "decks.tcgplayer.com",
    "User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:137.0) Gecko/20100101 Firefox/137.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "DNT": "1",
    "Sec-GPC": "1",
    "Connection": "keep-alive",
    "Cookie": SECRETS["tcgplayer"]["cookie"],
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Priority": "u=0, i",
    "TE": "trailers",
}


@DeckScraper.registered
class TcgPlayerDeckScraper(DeckScraper):
    """Scraper of TCG Player (old-site) decklist page.
    """
    HEADERS = HEADERS

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return "decks.tcgplayer.com/" in url.lower() and "/search" not in url.lower()

    @staticmethod
    @override
    def sanitize_url(url: str) -> str:
        return strip_url_query(url)

    @override
    def _parse_metadata(self) -> None:
        info_tag = self._soup.find("div", class_="viewDeckHeader")
        h1_tag = info_tag.find("h1")
        self._metadata["name"] = h1_tag.find("a").text.strip()
        h3_tag = info_tag.find("h3")
        self._metadata["author"] = h3_tag.text.strip().removeprefix("by ")
        for sub_tag in info_tag.find_all("div"):
            if "Format:" in sub_tag.text:
                fmt = sub_tag.find("a").text.strip().lower()
                self._update_fmt(fmt)
            elif "Last Modified On:" in sub_tag.text:
                _, date_text = sub_tag.text.strip().split("On: ", maxsplit=1)
                self._metadata["date"] = datetime.strptime(date_text, "%m/%d/%Y").date()

    @classmethod
    def _process_deck_tag(cls, deck_tag: Tag) -> list[Card]:
        cards = []
        card_tags = deck_tag.find_all("a", class_="subdeck-group__card")
        for card_tag in card_tags:
            quantity_tag, name_tag = card_tag.find_all("span")
            quantity = extract_int(quantity_tag.text)
            cards += cls.get_playset(cls.find_card(name_tag.text.strip()), quantity)
        return cards

    @override
    def _parse_deck(self) -> None:
        deck_tags = self._soup.find_all("div", class_="subdeck")
        for deck_tag in deck_tags:
            if deck_tag.find("h3").text.lower().startswith("command"):
                cards = self._process_deck_tag(deck_tag)
                for card in cards:
                    self._set_commander(card)
            elif deck_tag.find("h3").text.lower().startswith("sideboard"):
                self._sideboard = self._process_deck_tag(deck_tag)
            else:
                self._maindeck = self._process_deck_tag(deck_tag)


@DeckUrlsContainerScraper.registered
class TcgPlayerPlayerScraper(DeckUrlsContainerScraper):
    """Scraper of TCG Player (old-site) player search page.
    """
    HEADERS = HEADERS  # override
    CONTAINER_NAME = "TCGPlayer (old-site) player"  # override
    DECK_SCRAPERS = TcgPlayerDeckScraper,  # override
    DECK_URL_PREFIX = "https://decks.tcgplayer.com"  # override

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return ("decks.tcgplayer.com/magic/deck/search?" in url.lower()
                and"player=" in url)

    @override
    def _collect(self) -> list[str]:
        deck_tags = self._soup.find_all(
            "a", href=lambda h: h and "/magic/" in h and "/magic/deck" not in h)
        if not deck_tags:
            raise ScrapingError("Deck tags not found", scraper=type(self), url=self.url)
        return [deck_tag.attrs["href"] for deck_tag in deck_tags]


# INFINITE ########################################################################################
INFINITE_URL_PREFIX = "https://infinite.tcgplayer.com"


class TcgPlyerInfiniteDeckJsonParser(JsonBasedDeckParser):
    """Parser of an TCG Player Infinite deck JSON.
    """
    @override
    def _parse_metadata(self) -> None:
        self._metadata["name"] = self._deck_data["deck"]["name"]
        self._update_fmt(self._deck_data["deck"]["format"])
        self._metadata["author"] = self._deck_data["deck"]["playerName"]
        if date_text := self._deck_data["deck"]["created"]:
            with contextlib.suppress(dateutil.parser.ParserError):
                self._metadata["date"] = dateutil.parser.parse(date_text).date()
        if event_name := self._deck_data["deck"].get("eventName"):
            self._metadata["event"] = {}
            self._metadata["event"]["name"] = event_name
            if event_date := self._deck_data["deck"].get("eventDate"):
                self._metadata["event"]["date"] = dateutil.parser.parse(event_date).date()
            if event_level := self._deck_data["deck"].get("eventLevel"):
                self._metadata["event"]["level"] = event_level
            self._metadata["event"]["draws"] = self._deck_data["deck"]["eventDraws"]
            self._metadata["event"]["losses"] = self._deck_data["deck"]["eventLosses"]
            self._metadata["event"]["wins"] = self._deck_data["deck"]["eventWins"]
            self._metadata["event"]["placement_max"] = self._deck_data["deck"]["eventPlacementMax"]
            self._metadata["event"]["placement_min"] = self._deck_data["deck"]["eventPlacementMin"]
            if event_players := self._deck_data["deck"].get("eventPlayers"):
                self._metadata["event"]["players"] = event_players
            if event_rank := self._deck_data["deck"].get("eventRank"):
                self._metadata["event"]["rank"] = event_rank

    def _get_cardmap(self) -> dict[int, Card]:
        cardmap = {}
        for card_id, data in self._deck_data["cards"].items():
            name, tcgplayer_id, oracle_id = data["name"], data["tcgPlayerID"], data.get(
                "oracleID", "")
            card = self.find_card(name, tcgplayer_id=tcgplayer_id, oracle_id=oracle_id)
            cardmap[int(card_id)] = card
        return cardmap

    @override
    def _parse_deck(self) -> None:
        cardmap = self._get_cardmap()
        sub_decks = self._deck_data["deck"]["subDecks"]
        if command_zone := sub_decks.get("commandzone"):
            for item in command_zone:
                with contextlib.suppress(KeyError):
                    card_id, quantity = item["cardID"], item["quantity"]
                    self._set_commander(self.get_playset(cardmap[card_id], quantity)[0])

        for item in sub_decks["maindeck"]:
            with contextlib.suppress(KeyError):
                card_id, quantity = item["cardID"], item["quantity"]
                self._maindeck += self.get_playset(cardmap[card_id], quantity)

        if sideboard := sub_decks.get("sideboard"):
            for item in sideboard:
                with contextlib.suppress(KeyError):
                    card_id, quantity = item["cardID"], item["quantity"]
                    self._sideboard += self.get_playset(cardmap[card_id], quantity)


def _get_deck_data_from_api(
        url: str, api_url_template: str,
        scraper: Type[DeckScraper] | Type[DecksJsonContainerScraper]) -> Json:
    *_, decklist_id = url.split("/")
    if not all(ch.isdigit() for ch in decklist_id):
        raise ScrapingError(f"Invalid decklist ID: {decklist_id!r}. Must be an integer string")
    json_data, tries = {}, 0
    try:
        json_data = fetch_json(api_url_template.format(decklist_id), handle_http_errors=False)
        tries += 1
    except HTTPError as e:
        if "404 Not Found" in str(e):
            api_url_template += "&external=true"
            json_data = fetch_json(api_url_template.format(decklist_id))
            tries += 1
    except ReadTimeout:
        raise ScrapingError("Request timed out", scraper=scraper, url=url)

    if not json_data and tries < 2:
        throttle(*DeckScraper.THROTTLING)
        api_url_template += "&external=true"
        json_data = fetch_json(api_url_template.format(decklist_id))

    if not json_data or not json_data.get(
            "result") or json_data["result"].get("deck") == {"deck": {}}:
        raise ScrapingError("No deck data", scraper=scraper, url=url)
    return json_data["result"]


@DeckScraper.registered
class TcgPlayerInfiniteDeckScraper(DeckScraper):
    """Scraper of TCG Player Infinite decklist page.
    """
    API_URL_TEMPLATE = ("https://infinite-api.tcgplayer.com/deck/magic/{}/?source=infinite-"
                        "content&subDecks=true&cards=true&stats=true")  # override

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return ("infinite.tcgplayer.com/magic-the-gathering/deck/" in url.lower()
                or "tcgplayer.com/content/magic-the-gathering/deck/" in url.lower())

    @staticmethod
    @override
    def sanitize_url(url: str) -> str:
        return strip_url_query(url)

    @override
    def _get_data_from_api(self) -> Json:
        return _get_deck_data_from_api(self.url, self.API_URL_TEMPLATE, scraper=type(self))

    @override
    def _get_sub_parser(self) -> TcgPlyerInfiniteDeckJsonParser:
        return TcgPlyerInfiniteDeckJsonParser(self._data, self._metadata)

    @override
    def _parse_metadata(self) -> None:
        pass

    @override
    def _parse_deck(self) -> None:
        pass


@DeckUrlsContainerScraper.registered
class TcgPlayerInfinitePlayerScraper(DeckUrlsContainerScraper):
    """Scraper of TCG Player new-site player page.
    """
    CONTAINER_NAME = "TCGPlayer Infinite player"  # override
    # 100 rows is pretty arbitrary but tested to work
    API_URL_TEMPLATE = ("https://infinite-api.tcgplayer.com/content/decks/magic?source=infinite"
                        "-content&rows=100&format=&playerName"
                        "={}&latest=true&sort=created&order=desc")  # override
    DECK_SCRAPERS = TcgPlayerInfiniteDeckScraper,  # override
    DECK_URL_PREFIX = INFINITE_URL_PREFIX  # override

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return ("infinite.tcgplayer.com/magic-the-gathering/decks/player/" in url.lower()
                or "tcgplayer.com/content/magic-the-gathering/decks/player/" in url.lower())

    @staticmethod
    @override
    def sanitize_url(url: str) -> str:
        return strip_url_query(url)

    @override
    def _get_data_from_api(self) -> Json:
        *_, player_name = self.url.split("/")
        return fetch_json(self.API_URL_TEMPLATE.format(player_name))

    @override
    def _validate_data(self) -> None:
        super()._validate_data()
        if not self._data.get("result"):
            raise ScrapingError(f"No {self.short_name()} data", scraper=type(self), url=self.url)

    @override
    def _collect(self) -> list[str]:
        return [d["canonicalURL"] for d in self._data["result"]]


@DeckUrlsContainerScraper.registered
class TcgPlayerInfiniteAuthorSearchScraper(TcgPlayerInfinitePlayerScraper):
    """Scraper of TCG Player Infinite author search page.
    """
    CONTAINER_NAME = "TCGPlayer Infinite author search"  # override

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return (("infinite.tcgplayer.com/magic-the-gathering/decks/advanced-search" in url.lower()
                 or "tcgplayer.com/content/magic-the-gathering/decks/advanced-search" in url.lower())
                and "author=" in url.lower())

    @staticmethod
    @override
    def sanitize_url(url: str) -> str:
        return url.removesuffix("/")

    @override
    def _get_data_from_api(self) -> Json:
        *_, author = self.url.split("author=")
        if "&" in author:
            author, *_ = author.split("&")
        return fetch_json(self.API_URL_TEMPLATE.format(author))


@DeckUrlsContainerScraper.registered
class TcgPlayerInfiniteEventScraper(TcgPlayerInfinitePlayerScraper):
    """Scraper of TCG Player Infinite event page.
    """
    CONTAINER_NAME = "TCGPlayer Infinite event"  # override
    # 200 rows is pretty arbitrary but tested to work (even though usually events have fewer rows)
    API_URL_TEMPLATE = ("https://infinite-api.tcgplayer.com/content/decks/magic?source="
                        "infinite-content&rows=200&eventNames={}")  # override

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return ("infinite.tcgplayer.com/magic-the-gathering/events/event/" in url.lower()
                or "tcgplayer.com/content/magic-the-gathering/events/event/" in url.lower())


@DecksJsonContainerScraper.registered
class TcgPlayerInfiniteArticleScraper(DecksJsonContainerScraper):
    """Scraper of TCG Player Infinite article page.
    """
    _HOOK = "/magic-the-gathering/deck/"
    SELENIUM_PARAMS = {  # override
        "xpath": f"//a[contains(@href, '{_HOOK}')]",
        # "consent_xpath": ("//button[contains(@class, 'martech-button') and contains(@class, "
        #                   "'martech-medium') and contains(@class, 'martech-primary')]"),
        "wait_for_all": True,
        "scroll_down": True,
        "scroll_down_delay": 2.0,
        "timeout": 5.0
    }
    CONTAINER_NAME = "TCGPlayer Infinite article"  # override
    # NOTE: this doesn't override API_URL_TEMPLATE on purpose (not to skip soup fetching)
    ARTICLE_API_URL_TEMPLATE = TcgPlayerInfiniteDeckScraper.API_URL_TEMPLATE
    JSON_BASED_DECK_PARSER = TcgPlyerInfiniteDeckJsonParser  # override

    @property
    def _scroll_down_times(self) -> int:
        doubled = False
        tokens = "-decks", "-ranking", "-rankings"
        if any(t in self.url.lower() for t in tokens):
            doubled = True
        if any(t in self.url.lower() for t in ("top-", "best-")) and "-deck" in self.url.lower():
            doubled = True
        return 3 * SCROLL_DOWN_TIMES if doubled else SCROLL_DOWN_TIMES

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return (f"infinite.tcgplayer.com/article/" in url.lower()
                or "tcgplayer.com/content/article/" in url.lower())

    @staticmethod
    @override
    def sanitize_url(url: str) -> str:
        return strip_url_query(url)

    @override
    def _fetch_soup(self) -> None:
        try:
            self._soup, _, _ = fetch_dynamic_soup(
                self.url, **self.SELENIUM_PARAMS, scroll_down_times=self._scroll_down_times)
        except TimeoutException:
            raise ScrapingError(self._selenium_timeout_msg, scraper=type(self), url=self.url)

    @staticmethod
    def _naive_strip_url_query(url: str) -> str:
        if "?" in url:
            return url.split("?", maxsplit=1)[0].removesuffix("/")
        return url.removesuffix("/")

    @override
    def _collect(self) -> list[Json]:
        article_tag = self._soup.find("div", class_="article-body")
        if not article_tag:
            raise ScrapingError("Article tag not found", scraper=type(self), url=self.url)
        deck_urls = [
            self._naive_strip_url_query(t.attrs["href"]) for t in article_tag.find_all(
                "a", href=lambda h: h and self._HOOK in h)]

        decks_data = []
        for url in deck_urls:
            try:
                decks_data.append(
                    _get_deck_data_from_api(url, self.ARTICLE_API_URL_TEMPLATE, scraper=type(self)))
            except ScrapingError as err:
                _log.warning(f"Scraping failed with: {err!r}")
                continue
            throttle(*DeckScraper.THROTTLING)
        return decks_data


@HybridContainerScraper.registered
class TcgPlayerInfiniteAuthorScraper(HybridContainerScraper):
    """Scraper of TCG Player Infinite author page.
    """
    SELENIUM_PARAMS = {  # override
        "xpath": "//div[@class='grid']",
        # "consent_xpath": TcgPlayerInfiniteArticleScraper.SELENIUM_PARAMS["consent_xpath"],
    }
    CONTAINER_NAME = "TCGPlayer Infinite author"  # override
    API_URL_TEMPLATE = ("https://infinite-api.tcgplayer.com/content/author/{}/?source="
                        "infinite-content&rows=48&game=&format=")  # override
    CONTAINER_SCRAPERS = TcgPlayerInfiniteArticleScraper,  # override
    CONTAINER_URL_PREFIX = INFINITE_URL_PREFIX  # override

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return (("infinite.tcgplayer.com/author/" in url.lower()
                 or "tcgplayer.com/content/author/" in url.lower()) and not strip_url_query(
            url.lower()).endswith("/decks"))

    @staticmethod
    @override
    def sanitize_url(url: str) -> str:
        return strip_url_query(url)

    @override
    def _pre_parse(self) -> None:
        self._fetch_soup()
        self._validate_soup()
        self._data = self._get_data_from_api()
        self._validate_data()

    @classmethod
    def get_author_id(cls, soup: BeautifulSoup) -> str:
        script_tag = soup.find(
            "script", string=lambda s: s and 'identifier' in s and 'description' in s)
        if script_tag is None:
            raise ScrapingError("Author ID <script> tag not found", scraper=cls)
        try:
            data = json.loads(script_tag.text)
            return data.get("mainEntity", {}).get("identifier")
        except json.decoder.JSONDecodeError:
            raise ScrapingError(
                "Failed to obtain author ID from <script> tag's JavaScript", scraper=cls)

    @override
    def _get_data_from_api(self) -> Json:
        return fetch_json(self.API_URL_TEMPLATE.format(self.get_author_id(self._soup)))

    @override
    def _validate_data(self) -> None:
        super()._validate_data()
        if not self._data.get("result") or not self._data["result"].get("articles"):
            raise ScrapingError("No author or articles data", scraper=type(self), url=self.url)

    @override
    def _collect(self) -> tuple[list[str], list[Tag], list[Json], list[str]]:
        return [], [], [], [d["canonicalURL"] for d in self._data["result"]["articles"]]


@DeckUrlsContainerScraper.registered
class TcgPlayerInfiniteAuthorDecksPaneScraper(DeckUrlsContainerScraper):
    """Scraper of TCG Player Infinite author decks page.
    """
    SELENIUM_PARAMS = {  # override
        "xpath": TcgPlayerInfiniteAuthorScraper.SELENIUM_PARAMS["xpath"],
        # "consent_xpath": TcgPlayerInfiniteArticleScraper.SELENIUM_PARAMS["consent_xpath"],
    }
    CONTAINER_NAME = "TCGPlayer Infinite author decks pane"  # override
    # 200 rows is pretty arbitrary but tested to work (even though usually events have fewer rows)
    API_URL_TEMPLATE = ("https://infinite-api.tcgplayer.com/content/decks/?source"
                        "=infinite-content&rows=2008&authorID={}&latest=true&sort="
                        "created&order=desc")  # override
    DECK_SCRAPERS = TcgPlayerInfiniteDeckScraper,  # override
    DECK_URL_PREFIX = INFINITE_URL_PREFIX  # override

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return (("infinite.tcgplayer.com/author/" in url.lower()
                 or "tcgplayer.com/content/author/" in url.lower()) and strip_url_query(
            url.lower()).endswith("/decks"))

    @staticmethod
    @override
    def sanitize_url(url: str) -> str:
        return strip_url_query(url)

    @override
    def _pre_parse(self) -> None:
        self._fetch_soup()
        self._validate_soup()
        self._data = self._get_data_from_api()
        self._validate_data()

    @override
    def _get_data_from_api(self) -> Json:
        author_id = TcgPlayerInfiniteAuthorScraper.get_author_id(self._soup)
        return fetch_json(self.API_URL_TEMPLATE.format(author_id))

    @override
    def _validate_data(self) -> None:
        super()._validate_data()
        if not self._data.get("result"):
            raise ScrapingError("No decks data", scraper=type(self), url=self.url)

    @override
    def _collect(self) -> list[str]:
        return [d["canonicalURL"] for d in self._data["result"]]
