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
from mtg.deck.abc import DeckJsonParser
from mtg.deck.scrapers.abc import (
    DeckScraper, DeckUrlsContainerScraper, DecksJsonContainerScraper,
    HybridContainerScraper,
)
from mtg.lib.numbers import extract_int
from mtg.lib.scrape.core import (
    ScrapingError, fetch_json, get_path_segments, get_query_data, get_query_values,
    strip_url_query, throttle,
)
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
URL_PREFIX = "https://www.tcgplayer.com/content"


@DeckScraper.registered
class TcgPlayerOldDeckScraper(DeckScraper):
    """Scraper of TCG Player (old-site) decklist page.
    """
    # that type of URL now routes to a general "Decks and Event" page
    # therefore using Wayback Machine is the only viable option
    USE_WAYBACK = True
    HEADERS = HEADERS
    EXAMPLE_URLS = (
        "https://decks.tcgplayer.com/magic/standard/sbmtgdev/mono-red-mouse/1426437",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return "decks.tcgplayer.com/magic/" in url.lower() and "/search" not in url.lower()

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = super().normalize_url(url)
        return strip_url_query(url)

    @override
    def _parse_input_for_metadata(self) -> None:
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
    def _parse_input_for_decklist(self) -> None:
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


class TcgPlyerDeckJsonParser(DeckJsonParser):
    """Parser of an TCG Player deck JSON.
    """
    @override
    def _parse_input_for_metadata(self) -> None:
        self._metadata["name"] = self._deck_json["deck"]["name"]
        self._update_fmt(self._deck_json["deck"]["format"])
        self._metadata["author"] = self._deck_json["deck"]["playerName"]
        if date_text := self._deck_json["deck"]["created"]:
            with contextlib.suppress(dateutil.parser.ParserError):
                self._metadata["date"] = dateutil.parser.parse(date_text).date()
        if event_name := self._deck_json["deck"].get("eventName"):
            self._metadata["event"] = {}
            self._metadata["event"]["name"] = event_name
            if event_date := self._deck_json["deck"].get("eventDate"):
                self._metadata["event"]["date"] = dateutil.parser.parse(event_date).date()
            if event_level := self._deck_json["deck"].get("eventLevel"):
                self._metadata["event"]["level"] = event_level
            self._metadata["event"]["draws"] = self._deck_json["deck"]["eventDraws"]
            self._metadata["event"]["losses"] = self._deck_json["deck"]["eventLosses"]
            self._metadata["event"]["wins"] = self._deck_json["deck"]["eventWins"]
            self._metadata["event"]["placement_max"] = self._deck_json["deck"]["eventPlacementMax"]
            self._metadata["event"]["placement_min"] = self._deck_json["deck"]["eventPlacementMin"]
            if event_players := self._deck_json["deck"].get("eventPlayers"):
                self._metadata["event"]["players"] = event_players
            if event_rank := self._deck_json["deck"].get("eventRank"):
                self._metadata["event"]["rank"] = event_rank

    def _get_cardmap(self) -> dict[int, Card]:
        cardmap = {}
        for card_id, data in self._deck_json["cards"].items():
            name, tcgplayer_id, oracle_id = data["name"], data["tcgPlayerID"], data.get(
                "oracleID", "")
            card = self.find_card(name, tcgplayer_id=tcgplayer_id, oracle_id=oracle_id)
            cardmap[int(card_id)] = card
        return cardmap

    @override
    def _parse_input_for_decklist(self) -> None:
        cardmap = self._get_cardmap()
        sub_decks = self._deck_json["deck"]["subDecks"]
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
        url: str,
        scraper: Type[DeckScraper] | Type[DecksJsonContainerScraper]) -> Json:
    api_url_template = (
        "https://infinite-api.tcgplayer.com/deck/magic/{}/?source=infinite-"
        "content&subDecks=true&cards=true&stats=true"
    )
    *_, decklist_id = get_path_segments(url)
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
class TcgPlayerDeckScraper(DeckScraper):
    """Scraper of TCG Player decklist page.
    """
    JSON_FROM_API = True  # override
    REPLACE_URL_HOOKS = (
        (
            "infinite.tcgplayer.com/magic-the-gathering/",
            "www.tcgplayer.com/content/magic-the-gathering/",
        ),
        (
            "channelfireball.com/magic-the-gathering",
            "tcgplayer.com/content/magic-the-gathering/",
        ),
    )
    EXAMPLE_URLS = (
        "https://infinite.tcgplayer.com/magic-the-gathering/deck/Izzet-Prowess/534825",
        "https://www.tcgplayer.com/content/magic-the-gathering/deck/Izzet-Prowess/534825",
        "https://www.channelfireball.com/magic-the-gathering/deck/Timeless-Grixis/481595?external=undefined",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        url = url.lower()
        if not (
            "infinite.tcgplayer.com/magic-the-gathering/deck/" in url
            or "tcgplayer.com/content/magic-the-gathering/deck/" in url
            or "channelfireball.com/magic-the-gathering/deck/" in url
        ):
            return False
        try:
            *_, decklist_id = get_path_segments(url)
            if all(ch.isdigit() for ch in decklist_id):
                return True
            return False
        except ValueError:
            return False

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = super().normalize_url(url)
        url = strip_url_query(url)
        return url.replace(*cls.REPLACE_URL_HOOKS[0]).replace(*cls.REPLACE_URL_HOOKS[1])

    @override
    def _fetch_json(self) -> None:
        self._json = _get_deck_data_from_api(self.url, scraper=type(self))

    @override
    def _get_sub_parser(self) -> TcgPlyerDeckJsonParser:
        return TcgPlyerDeckJsonParser(self._json, self._metadata)

    @override
    def _parse_input_for_metadata(self) -> None:
        pass

    @override
    def _parse_input_for_decklist(self) -> None:
        pass


@DeckUrlsContainerScraper.registered
class TcgPlayerPlayerScraper(DeckUrlsContainerScraper):
    """Scraper of TCG Player's player page.
    """
    CONTAINER_NAME = "TCGPlayer player"  # override
    JSON_FROM_API = True  # override
    # 100 rows is pretty arbitrary but tested to work
    _API_URL_TEMPLATE = (
        "https://infinite-api.tcgplayer.com/content/decks/magic?source=infinite"
        "-content&rows=100&format=&playerName={}&latest=true&sort=created&order=desc"
    )
    _VALID_URL_HOOK = "magic-the-gathering/decks/player/"
    DECK_SCRAPER_TYPES = TcgPlayerDeckScraper,  # override
    DECK_URL_PREFIX = URL_PREFIX  # override
    EXAMPLE_URLS = (
        "https://infinite.tcgplayer.com/magic-the-gathering/decks/player/SBMTGDev/",
        "https://www.channelfireball.com/magic-the-gathering/decks/player/Martin%20Juza",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        hook = cls._VALID_URL_HOOK
        if not (
            f"infinite.tcgplayer.com/{hook}" in url.lower()
            or f"tcgplayer.com/content/{hook}" in url.lower()
            or f"channelfireball.com/{hook}" in url.lower()
        ):
            return False
        try:
            *_, name = get_path_segments(url)
            return True
        except ValueError:
            return False

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        return TcgPlayerDeckScraper.normalize_url(url)

    @override
    def _fetch_json(self) -> None:
        *_, name = get_path_segments(self.url)
        self._json = fetch_json(self._API_URL_TEMPLATE.format(name))

    @override
    def _validate_json(self) -> None:
        super()._validate_json()
        if not self._json.get("result"):
            raise ScrapingError(f"No {self.short_name()} data", scraper=type(self), url=self.url)

    @override
    def _parse_input_for_decks_data(self) -> None:
        self._deck_urls = [d["canonicalURL"] for d in self._json["result"]]


@DeckUrlsContainerScraper.registered
class TcgPlayerAuthorSearchScraper(TcgPlayerPlayerScraper):
    """Scraper of TCG Player author search page.
    """
    CONTAINER_NAME = "TCGPlayer author search"  # override
    EXAMPLE_URLS = (
        "https://www.tcgplayer.com/content/magic-the-gathering/decks/advanced-search/?author=SBMTGDev&p=1",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        url = url.lower()
        query_params = get_query_data(url)
        return (
            (
                "infinite.tcgplayer.com/magic-the-gathering/decks/advanced-search" in url
                or "tcgplayer.com/content/magic-the-gathering/decks/advanced-search" in url
            ) and "author" in query_params
        )

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = DeckScraper.normalize_url(url)
        return url.replace(*TcgPlayerDeckScraper.REPLACE_URL_HOOKS[0]).replace(
            *TcgPlayerDeckScraper.REPLACE_URL_HOOKS[1])

    @override
    def _fetch_json(self) -> Json:
        [author] = get_query_values(self.url, "author")
        return fetch_json(self._API_URL_TEMPLATE.format(author))


@DeckUrlsContainerScraper.registered
class TcgPlayerEventScraper(TcgPlayerPlayerScraper):
    """Scraper of TCG Player event page.
    """
    CONTAINER_NAME = "TCGPlayer event"  # override
    # 200 rows is pretty arbitrary but tested to work (even though usually events have fewer rows)
    _API_URL_TEMPLATE = (
        "https://infinite-api.tcgplayer.com/content/decks/magic?source="
        "infinite-content&rows=200&eventNames={}"
    )  # override
    _VALID_URL_HOOK = "magic-the-gathering/decks/events/event/"  # override
    EXAMPLE_URLS = (
        "https://www.tcgplayer.com/content/magic-the-gathering/events/event/MTGO%20Standard%20Challenge%2032%20-%2011-12-2024/",
    )


@DecksJsonContainerScraper.registered
class TcgPlayerArticleScraper(DecksJsonContainerScraper):
    """Scraper of TCG Player article page.
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
    CONTAINER_NAME = "TCGPlayer article"  # override
    DECK_JSON_PARSER_TYPE = TcgPlyerDeckJsonParser  # override
    EXAMPLE_URLS = (
        "https://www.tcgplayer.com/content/article/Critical-Role-Plays-Commander/aaa4eb52-0670-4a1f-9eda-87c0784c3c8f/",
        "https://www.channelfireball.com/article/MTG-Deck-Guide-Standard-Gruul-Aggro/bd06ac65-bb14-442c-aed5-cb9195861496/",
    )

    @property
    def _scroll_down_times(self) -> int:
        doubled = False
        tokens = "-decks", "-ranking", "-rankings"
        if any(t in self.url.lower() for t in tokens):
            doubled = True
        if any(t in self.url.lower() for t in ("top-", "best-")) and "-deck" in self.url.lower():
            doubled = True
        return 3 * SCROLL_DOWN_TIMES if doubled else SCROLL_DOWN_TIMES

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return (
            f"infinite.tcgplayer.com/article/" in url.lower()
            or "tcgplayer.com/content/article/" in url.lower()
            or "channelfireball.com/article/" in url.lower()
        )

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        return TcgPlayerDeckScraper.normalize_url(url)

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
    def _parse_input_for_decks_data(self) -> None:
        article_tag = self._soup.find("div", class_="article-body")
        if not article_tag:
            raise ScrapingError("Article tag not found", scraper=type(self), url=self.url)
        deck_urls = [
            self._naive_strip_url_query(t.attrs["href"]) for t in article_tag.find_all(
                "a", href=lambda h: h and self._HOOK in h)]

        for url in deck_urls:
            try:
                self._decks_json.append(_get_deck_data_from_api(url, scraper=type(self)))
            except ScrapingError as err:
                _log.warning(f"Scraping failed with: {err!r}")
                continue
            throttle(*DeckScraper.THROTTLING)


@HybridContainerScraper.registered
class TcgPlayerAuthorScraper(HybridContainerScraper):
    """Scraper of TCG Player author page.
    """
    SELENIUM_PARAMS = {  # override
        "xpath": "//div[@class='grid']",
        # "consent_xpath": TcgPlayerInfiniteArticleScraper.SELENIUM_PARAMS["consent_xpath"],
    }
    CONTAINER_NAME = "TCGPlayer author"  # override
    CONTAINER_SCRAPER_TYPES = TcgPlayerArticleScraper,  # override
    CONTAINER_URL_PREFIX = URL_PREFIX  # override
    EXAMPLE_URLS = (
        "https://www.tcgplayer.com/content/author/Critical-Role",
        "https://www.channelfireball.com/author/Frank-Karsten/7f203152-211a-478d-8fee-464c2aeca2cd",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return (
            (
                "infinite.tcgplayer.com/author/" in url.lower()
                or "tcgplayer.com/content/author/" in url.lower()
                or "channelfireball.com/author/" in url.lower()
            ) and not strip_url_query(url.lower()).endswith("/decks")
        )

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        return TcgPlayerDeckScraper.normalize_url(url)

    @override
    def _pre_parse(self) -> None:
        self._fetch_soup()
        self._validate_soup()
        self._fetch_json()
        self._validate_json()

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
    def _fetch_json(self) -> None:
        author_id = self.get_author_id(self._soup)
        api_url = (
            f"https://infinite-api.tcgplayer.com/content/author/{author_id}/?source="
            "infinite-content&rows=48&game=&format="
        )
        self._json = fetch_json(api_url)

    @override
    def _validate_json(self) -> None:
        super()._validate_json()
        if not self._json.get("result") or not self._json["result"].get("articles"):
            raise ScrapingError("No author or articles data", scraper=type(self), url=self.url)

    @override
    def _parse_input_for_decks_data(self) -> None:
        self._container_urls = [d["canonicalURL"] for d in self._json["result"]["articles"]]


@DeckUrlsContainerScraper.registered
class TcgPlayerAuthorDecksPaneScraper(DeckUrlsContainerScraper):
    """Scraper of TCG Player author decks page.
    """
    SELENIUM_PARAMS = {  # override
        "xpath": TcgPlayerAuthorScraper.SELENIUM_PARAMS["xpath"],
        # "consent_xpath": TcgPlayerInfiniteArticleScraper.SELENIUM_PARAMS["consent_xpath"],
    }
    CONTAINER_NAME = "TCGPlayer author decks pane"  # override
    DECK_SCRAPER_TYPES = TcgPlayerDeckScraper,  # override
    DECK_URL_PREFIX = URL_PREFIX  # override
    EXAMPLE_URLS = (
        "https://www.tcgplayer.com/content/author/Reid-Duke/decks/",
        # old page's format that now redirects to the new one
        "https://decks.tcgplayer.com/magic/deck/search?player=the-commander-s-quarters",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        url = url.lower()
        params = get_query_data(url)
        return (
            (
                "infinite.tcgplayer.com/author/" in url
                or "tcgplayer.com/content/author/" in url
            ) and strip_url_query(url).endswith("/decks")
            or (
                "decks.tcgplayer.com/magic/deck/search?" in url
                and "player" in params
            )
        )

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = url.lower()
        if "decks.tcgplayer.com/magic/deck/search?" in url:
            [player_name] = get_query_values(url, "player")
            url = f"https://www.tcgplayer.com/content/author/{player_name}/decks/"
        return TcgPlayerDeckScraper.normalize_url(url)

    @override
    def _pre_parse(self) -> None:
        self._fetch_soup()
        self._validate_soup()
        self._fetch_json()
        self._validate_json()

    @override
    def _fetch_json(self) -> None:
        author_id = TcgPlayerAuthorScraper.get_author_id(self._soup)
        # 200 rows is pretty arbitrary but tested to work (even though usually events have fewer rows)
        api_url = (
            "https://infinite-api.tcgplayer.com/content/decks/?source"
            f"=infinite-content&rows=2008&authorID={author_id}&latest=true&sort=created&order=desc"
        )
        self._json = fetch_json(api_url)

    @override
    def _validate_json(self) -> None:
        super()._validate_json()
        if not self._json.get("result"):
            raise ScrapingError("No decks data", scraper=type(self), url=self.url)

    @override
    def _parse_input_for_decks_data(self) -> None:
        self._deck_urls = [d["canonicalURL"] for d in self._json["result"]]
