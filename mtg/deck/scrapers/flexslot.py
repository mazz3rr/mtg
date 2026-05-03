"""

    mtg.deck.scrapers.flexslot
    ~~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape Flexslot.gg decklists.

    @author: mazz3rr

"""
import logging
from typing import override

import dateutil.parser
from bs4 import BeautifulSoup

from mtg.constants import Json, SECRETS
from mtg.deck.abc import DeckJsonParser
from mtg.deck.scrapers.abc import (
    DeckScraper, DeckUrlsContainerScraper, DecksJsonContainerScraper,
    HybridContainerScraper,
)
from mtg.lib.scrape.core import (
    InaccessiblePage, ScrapingError, fetch_json,
    is_more_than_root_path, strip_url_query,
)
from mtg.yt.discover import UrlHook

_log = logging.getLogger(__name__)
HEADERS = {
    "Host": "api.flexslot.gg",
    "User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:135.0) Gecko/20100101 Firefox/135.0",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "X-Cookie-Consent": "true",
    "X-API-Key": SECRETS["flexslot"]["api_key"],
    "X-CSRFToken" : "",
    "Origin": "https://flexslot.gg",
    "Connection": "keep-alive",
    "Referer": "https://flexslot.gg/",
    "Cookie": SECRETS["flexslot"]["cookie"],
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
    "TE": "trailers",
}
URL_HOOKS = (
    # deck
    UrlHook(
        ('"flexslot.gg/decks/"', ),
    ),
    # sideboard
    UrlHook(
        ('"flexslot.gg/sideboards/"', ),
    ),
    # article
    UrlHook(
        ('"flexslot.gg/article/"', ),
    ),
    # user
    UrlHook(
        ('"flexslot.gg/u/"', ),
    ),
)


def _get_json_data(url: str, **suffixes) -> Json:
    domain, api_domain = "https://flexslot.gg", "https://api.flexslot.gg"
    if suffixes:
        domain += suffixes["domain_suffix"]
        api_domain += suffixes["api_domain_suffix"]
    return fetch_json(url.replace(domain, api_domain), headers=HEADERS)


class FlexslotDeckJsonParser(DeckJsonParser):
    """Parser of Flexslot.gg deck JSON data.
    """
    @override
    def _parse_input_for_metadata(self) -> None:
        self._metadata["name"] = self._deck_json["name"]
        self._metadata["author"] = self._deck_json["creator"]
        self._update_fmt(self._deck_json["format"].lower())
        self._metadata["date"] = dateutil.parser.parse(self._deck_json["date_updated"]).date()
        self._metadata["likes"] = self._deck_json["likes"]
        self._metadata["views"] = self._deck_json["pageviews"]
        if event_name := self._deck_json.get("event_name"):
            self._metadata["event"] = {"name": event_name}
            if event_date := self._deck_json.get("event_date"):
                self._metadata["event"]["date"] = dateutil.parser.parse(event_date).date()
            if player := self._deck_json.get("player"):
                self._metadata["event"]["player"] = player
            if rank := self._deck_json.get("rank"):
                self._metadata["event"]["rank"] = rank
        if archetype := self._deck_json.get("archetype"):
            self._update_archetype_or_theme(archetype)

    def _parse_card_json(self, card_json: Json) -> None:
        quantity = card_json["quantity"]
        name = card_json["card"]["name"]
        scryfall_id = card_json["card"]["id"]
        card = self.find_card(name, scryfall_id=scryfall_id)
        playset = self.get_playset(card, quantity)
        portion = card_json["deck_portion"]
        if portion == "side":
            self._sideboard.extend(playset)
        elif portion == "main":
            self._maindeck.extend(playset)

    @override
    def _parse_input_for_decklist(self) -> None:
        for card_json in self._deck_json["deck_card_maps"]:
            self._parse_card_json(card_json)
        self._derive_commander_from_sideboard()



# This is how you obtain deck data now:

# from mtg.lib.scrape.dynamic import fetch_dynamic_soup
# xpath = "//script[contains(text(), 'api.scryfall.com/cards/')]"
# url = "https://flexslot.gg/decks/243fc88f-1fca-41ae-a81a-9503347ce85c"
# r = fetch_dynamic_soup(url, xpath=xpath)
# soup, *_ = r
# 2026-05-03 23:51:57,761 [mtg.lib.scrape.dynamic] INFO: Webdriving using Chrome to: 'https://flexslot.gg/decks/243fc88f-1fca-41ae-a81a-9503347ce85c'...
# 2026-05-03 23:52:00,454 [mtg.lib.scrape.dynamic] INFO: Page has been loaded and XPath-specified element(s) is present
# 2026-05-03 23:52:00,688 [mtg.lib.time] INFO: Completed fetching dynamic soup in 3.734 second(s)
# markup = str(soup)
# import njsparser
# fd = njsparser.BeautifulFD(markup)
# 2026-05-03 23:52:29,092 [njsparser] WARNING: Couldn't find an appropriate type for given class `None`. Giving `Element`.
# 2026-05-03 23:52:29,092 [njsparser] WARNING: Couldn't find an appropriate type for given class `None`. Giving `Element`.
# import json
# parsed_json = json.dumps(fd, indent=4, default=njsparser.default)
# parsed_data = json.loads(parsed_json)
# from mtg.lib.json import Node
# node = Node(parsed_data)
# found = node.find(lambda n: n.is_text and 'Walking Ballista' in n.data)
# found.path
# "/['52']['value'][3]['children'][3]['children'][3]['children'][3]['initialData']['data']['text_list']"
# fa = found.ancestors[0]
# deck_data = fa.data


@DeckScraper.registered
class FlexslotDeckScraper(DeckScraper):
    """Scraper of Flexslot.gg decklist page.
    """
    EXAMPLE_URLS = (
        "https://flexslot.gg/decks/243fc88f-1fca-41ae-a81a-9503347ce85c",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return is_more_than_root_path(url, "flexslot.gg/decks/")

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = super().normalize_url(url)
        return strip_url_query(url).removesuffix("/view")

    @override
    def _pre_parse(self) -> None:
        json_data = _get_json_data(self.url)
        if not json_data or not json_data.get("data"):
            raise ScrapingError("No deck data", scraper=type(self), url=self.url)
        self._json = json_data["data"]

    @override
    def _get_sub_parser(self) -> FlexslotDeckJsonParser:
        return FlexslotDeckJsonParser(self._json, self._metadata)

    @override
    def _parse_input_for_metadata(self) -> None:
        pass

    @override
    def _parse_input_for_decklist(self) -> None:
        pass


@DecksJsonContainerScraper.registered
class FlexslotSideboardScraper(DecksJsonContainerScraper):
    """Scraper of Flexslot.gg sideboard guide page.
    """
    CONTAINER_NAME = "Flexslot sideboard"  # override
    DECK_JSON_PARSER_TYPE = FlexslotDeckJsonParser  # override
    EXAMPLE_URLS = (
        "https://flexslot.gg/sideboards/7861",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return is_more_than_root_path(url, "flexslot.gg/sideboards/")

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        return FlexslotDeckScraper.normalize_url(url)

    @override
    def _pre_parse(self) -> None:
        json_data = _get_json_data(self.url)
        if not json_data or not json_data.get("data"):
            raise ScrapingError("No sideboard data", scraper=type(self), url=self.url)
        json_data = json_data["data"]
        if not json_data.get("decks"):
            if json_data.get("visibility") == "Patreon Exclusive":
                raise InaccessiblePage(
                    "Content paywalled (Patreon exclusive)", type(self), self.url)
            raise ScrapingError("No decks data", type(self), self.url)
        self._json = json_data

    @override
    def _parse_input_for_decks_data(self) -> None:
        self._decks_json = self._json["decks"]


@HybridContainerScraper.registered
class FlexslotArticleScraper(HybridContainerScraper):
    """Scraper of Flexslot article page.
    """
    CONTAINER_NAME = "Flexslot article"  # override
    CONTAINER_SCRAPER_TYPES = FlexslotSideboardScraper,  # override
    EXAMPLE_URLS = (
        "https://flexslot.gg/articles/a3222f36-64f1-43b1-9b5a-94dffa76459a",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return is_more_than_root_path(url, "flexslot.gg/articles/")

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        return FlexslotDeckScraper.normalize_url(url)

    @override
    def _pre_parse(self) -> None:
        json_data = _get_json_data(
            self.url, domain_suffix="/article/", api_domain_suffix="/blogposts/")
        if not json_data or not json_data.get("data"):
            raise ScrapingError("No article data", scraper=type(self), url=self.url)
        self._json = json_data["data"]
        if not self._json.get("content"):
            raise ScrapingError("No article HTML content data", type(self), self.url)
        self._soup = BeautifulSoup(self._json["content"], "lxml")

    @override
    def _parse_input_for_metadata(self) -> None:
        if author := self._json.get("author_name") or self._json.get("author_username"):
            self._metadata["author"] = author
        if date := self._json.get("date_updated") or self._json.get(
                "date_created") or self._json.get("date_published"):
            self._metadata["date"] = dateutil.parser.parse(date).date()
        if title := self._json.get("title"):
            self._metadata.setdefault("article", {})["title"] = title
        if page_views := self._json.get("pageviews"):
            self._metadata.setdefault("article", {})["page_views"] = page_views
        if likes := self._json.get("likes"):
            self._metadata.setdefault("article", {})["likes"] = likes
        if tags := self._json.get("tags"):
            self._metadata.setdefault("article", {})["tags"] = self.normalize_metadata_deck_tags(tags)

    @override
    def _parse_input_for_decks_data(self) -> None:
        deck_urls, container_urls = self._find_links_in_tags()
        deck_urls2, container_urls2 = self._sift_links(
            *[t.text for t in self._soup.select("h4 > strong > u")])
        self._deck_urls = deck_urls + deck_urls2
        self._container_urls = container_urls + container_urls2


# TODO: add articles (once Flexslot.gg actually adds them to a user page)
@HybridContainerScraper.registered
class FlexslotUserScraper(HybridContainerScraper):
    """Scraper of Flexslot user page.
    """
    CONTAINER_NAME = "Flexslot user"  # override
    THROTTLING = DeckUrlsContainerScraper.THROTTLING * 2  # override
    DECK_SCRAPER_TYPES = FlexslotDeckScraper,  # override
    CONTAINER_SCRAPER_TYPES = FlexslotSideboardScraper,  #override
    API_URL_TEMPLATE = "https://api.flexslot.gg/{}/search/?firebase_user_id={}&page=1"
    EXAMPLE_URLS = (
        "https://flexslot.gg/u/YungDingo",
    )

    def __init__(self, url: str, metadata: Json | None = None) -> None:
        super().__init__(url, metadata)
        self._decks_json, self._sideboards_json = None, None

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return "flexslot.gg/u/" in url.lower()

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = super().normalize_url(url)
        return strip_url_query(url)

    @override
    def _pre_parse(self) -> None:
        user_data = _get_json_data(
            self.url, domain_suffix="/u/",
            api_domain_suffix="/users/get_user_short_by_name/")
        if not user_data.get("firebase_id"):
            raise ScrapingError("No user Firebase ID data", type(self), self.url)
        user_id = user_data["firebase_id"]
        self._decks_json = fetch_json(
            self.API_URL_TEMPLATE.format("decks", user_id), headers=HEADERS)
        self._sideboards_json = fetch_json(
            self.API_URL_TEMPLATE.format("sideboards", user_id), headers=HEADERS)

    @staticmethod
    def _check_visibility(data: list[dict]) -> None:
        visibilities = {d["visibility"] for d in data}
        known = {"Public", "Patreon Exclusive", "Paid Exclusive"}
        if unexpected := {v for v in visibilities if v not in known}:
            _log.warning(f"Unexpected data visibilities: {unexpected}")

    @classmethod
    def _process_data(cls, data: list[dict], template: str) -> list[str]:
        cls._check_visibility(data)
        return [template.format(d["id"]) for d in data if d["visibility"] == "Public"]

    def _get_deck_urls(self) -> list[str]:
        template = "https://flexslot.gg/decks/{}"
        if decks := self._decks_json.get("decks", []):
            return self._process_data(decks, template)
        return []

    def _get_sideboard_urls(self) -> list[str]:
        template = "https://flexslot.gg/sideboards/{}"
        if sideboards := self._sideboards_json.get("sideboards", []):
            return self._process_data(sideboards, template)
        return []

    @override
    def _parse_input_for_decks_data(self) -> None:
        self._deck_urls, self._container_urls = self._get_deck_urls(), self._get_sideboard_urls()
