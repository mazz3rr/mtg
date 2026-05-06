"""

    mtg.deck.scrapers.flexslot
    ~~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape Flexslot.gg decklists.

    @author: mazz3rr

"""
import json
import logging
from typing import Self, override

import dateutil.parser
import njsparser
from bs4 import BeautifulSoup

from mtg.constants import Json, SECRETS
from mtg.deck.abc import DeckJsonParser
from mtg.deck.scrapers.abc import (
    DeckScraper, DeckUrlsContainerScraper, DecksJsonContainerScraper,
    HybridContainerScraper,
)
from mtg.lib.json import Node, node_from_njs_fd_markup
from mtg.lib.scrape.core import (
    InaccessiblePage, ScrapingError, extract_url, fetch_json,
    is_more_than_root_path, strip_url_query,
)
from mtg.yt.discover import UrlHook

_log = logging.getLogger(__name__)
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


@DeckScraper.registered
class FlexslotDeckScraper(DeckScraper):
    """Scraper of Flexslot.gg decklist page.
    """
    JSON_FROM_SOUP = True
    SELENIUM_PARAMS = {
        "xpath": "//script[contains(text(), 'api.scryfall.com/cards/')]",
    }
    EXAMPLE_URLS = (
        "https://flexslot.gg/decks/243fc88f-1fca-41ae-a81a-9503347ce85c",
    )

    @property
    def _nodepath(self) -> str:
        return "['initialData']['data']['text_list']"

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return is_more_than_root_path(url, "flexslot.gg", "decks")

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = super().normalize_url(url)
        return strip_url_query(url).removesuffix("/view")

    @override
    def _extract_json(self) -> None:
        node = node_from_njs_fd_markup(self._markup)
        found = node.find_by_path(self._nodepath, mode="end")
        if not found:
            raise ScrapingError("No deck data", scraper=type(self), url=self.url)
        self._json = found.ancestors[0].data

    @override
    def _validate_json(self) -> None:
        if not self._json.get("text_list"):
            raise ScrapingError("No text list data", scraper=type(self), url=self.url)

    @override
    def _parse_input_for_metadata(self) -> None:
        self._metadata["name"] = self._json["name"]
        if firebase_user := self._json.get("firebase_user"):
            self._metadata["author"] = firebase_user["username"]
            self._metadata["author_real_name"] = firebase_user["name"]
        self._update_fmt(self._json["format"].lower())
        self._metadata["date"] = dateutil.parser.parse(self._json["updated_at"]).date()
        self._metadata["likes"] = self._json["likes"]
        self._metadata["views"] = self._json["pageviews"]
        if deck_type := self._json.get("decktype"):
            self._metadata["deck_type"] = deck_type
        # used to be present in the legacy data
        if event_name := self._json.get("event_name"):
            self._metadata["event"] = {"name": event_name}
            if event_date := self._json.get("event_date"):
                self._metadata["event"]["date"] = dateutil.parser.parse(event_date).date()
            if player := self._json.get("player"):
                self._metadata["event"]["player"] = player
            if rank := self._json.get("rank"):
                self._metadata["event"]["rank"] = rank

    @override
    def _parse_input_for_decklist(self) -> None:
        self._decklist = self._json["text_list"].replace(
            "Commander:", "Commander").replace("Companion:", "Companion").replace(
            "Maindeck:", "Deck").replace("Sideboard:", "Sideboard")


@DeckScraper.registered
class FlexslotSideboardDeckScraper(FlexslotDeckScraper):
    """Scraper of Flexslot.gg sideboard guide decklist page.
    """
    SELENIUM_PARAMS = {
        "xpath": FlexslotDeckScraper.SELENIUM_PARAMS["xpath"],
        "halt_xpaths": (
            "//div[contains(text(), 'Exclusive Content') "
            "or contains(text(), 'Subscriber Exclusive') "
            "or contains(text(), 'Paid Exclusive') "
            "or contains(text(), 'Content Not Found')]",
            "//h3[contains(text(), 'No Cards in Deck')]",
        )
    }
    EXAMPLE_URLS = (
        "https://flexslot.gg/sideboards/ca3f1dae-a1d5-4e11-91f2-57e2f0b5fa9a",
    )

    @property
    @override
    def _nodepath(self) -> str:
        return "['initialSideboardData']['data']['deck']['text_list']"

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return is_more_than_root_path(url, "flexslot.gg", "sideboards")


@HybridContainerScraper.registered
class FlexslotArticleScraper(HybridContainerScraper):
    """Scraper of Flexslot article page.
    """
    SELENIUM_PARAMS = {
        "xpath": "//div[contains(concat(' ', normalize-space(@class), ' '), ' ProseMirror ')]",
    }
    PARSE_NJS_FD = True
    CONTAINER_NAME = "Flexslot article"  # override
    EXAMPLE_URLS = (
        "https://flexslot.gg/articles/a3222f36-64f1-43b1-9b5a-94dffa76459a",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return is_more_than_root_path(url, "flexslot.gg", "articles")

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        return FlexslotDeckScraper.normalize_url(url)

    @override
    def _parse_input_for_metadata(self) -> None:
        tokens = "title", "author_name", "author_username", "updated_at", "pageviews", "likes"
        data_node = self._node.find(lambda n: n.is_dict and all(t in n.data for t in tokens))
        if not data_node:
            raise ScrapingError("No article metadata found", scraper=type(self), url=self.url)
        data = data_node.data
        if author := data.get("author_name") or data.get("author_username"):
            self._metadata["author"] = author
        if date := data.get("date_updated") or data.get(
                "date_created") or data.get("date_published"):
            self._metadata["date"] = dateutil.parser.parse(date).date()
        # article-specific metadata
        self._metadata["article"] = {}
        self._metadata["article"]["title"] = data["title"]
        self._metadata["article"]["author"] = data["author_username"]
        self._metadata["article"]["author_real_name"] = data["author_name"]
        self._metadata["article"]["date"] = dateutil.parser.parse(data["updated_at"]).date()
        if tags := data.get("tags"):
            self._metadata["article"]["tags"] = tags
        self._metadata["article"]["views"] = data["pageviews"]
        self._metadata["article"]["likes"] = data["likes"]

    @override
    def _parse_input_for_decks_data(self) -> None:
        tokens = "type", "doc", "content", "heading", "paragraph", "text"
        found = self._node.find(lambda n: n.is_str and all(t in n.data for t in tokens))
        if not found:
            raise ScrapingError("No article data found", scraper=type(self), url=self.url)
        data_node = Node(json.loads(found.data))
        article_text  = "\n".join(n.data for n in data_node.find_all(lambda n: n.key == "text"))
        links = [extract_url(l) for l in article_text.splitlines()]
        self._deck_urls, self._container_urls = self._sift_links(*[l for l in links if l])


# # TODO: add articles (once Flexslot.gg actually adds them to a user page)
# @HybridContainerScraper.registered
# class FlexslotUserScraper(HybridContainerScraper):
#     """Scraper of Flexslot user page.
#     """
#     CONTAINER_NAME = "Flexslot user"  # override
#     THROTTLING = DeckUrlsContainerScraper.THROTTLING * 2  # override
#     DECK_SCRAPER_TYPES = FlexslotDeckScraper,  # override
#     CONTAINER_SCRAPER_TYPES = FlexslotSideboardScraper,  #override
#     API_URL_TEMPLATE = "https://api.flexslot.gg/{}/search/?firebase_user_id={}&page=1"
#     EXAMPLE_URLS = (
#         "https://flexslot.gg/u/YungDingo",
#     )
#
#     def __init__(self, url: str, metadata: Json | None = None) -> None:
#         super().__init__(url, metadata)
#         self._decks_json, self._sideboards_json = None, None
#
#     @classmethod
#     @override
#     def is_valid_url(cls, url: str) -> bool:
#         return "flexslot.gg/u/" in url.lower()
#
#     @classmethod
#     @override
#     def normalize_url(cls, url: str) -> str:
#         url = super().normalize_url(url)
#         return strip_url_query(url)
#
#     @override
#     def _pre_parse(self) -> None:
#         user_data = _get_json_data(
#             self.url, domain_suffix="/u/",
#             api_domain_suffix="/users/get_user_short_by_name/")
#         if not user_data.get("firebase_id"):
#             raise ScrapingError("No user Firebase ID data", type(self), self.url)
#         user_id = user_data["firebase_id"]
#         self._decks_json = fetch_json(
#             self.API_URL_TEMPLATE.format("decks", user_id), headers=HEADERS)
#         self._sideboards_json = fetch_json(
#             self.API_URL_TEMPLATE.format("sideboards", user_id), headers=HEADERS)
#
#     @staticmethod
#     def _check_visibility(data: list[dict]) -> None:
#         visibilities = {d["visibility"] for d in data}
#         known = {"Public", "Patreon Exclusive", "Paid Exclusive"}
#         if unexpected := {v for v in visibilities if v not in known}:
#             _log.warning(f"Unexpected data visibilities: {unexpected}")
#
#     @classmethod
#     def _process_data(cls, data: list[dict], template: str) -> list[str]:
#         cls._check_visibility(data)
#         return [template.format(d["id"]) for d in data if d["visibility"] == "Public"]
#
#     def _get_deck_urls(self) -> list[str]:
#         template = "https://flexslot.gg/decks/{}"
#         if decks := self._decks_json.get("decks", []):
#             return self._process_data(decks, template)
#         return []
#
#     def _get_sideboard_urls(self) -> list[str]:
#         template = "https://flexslot.gg/sideboards/{}"
#         if sideboards := self._sideboards_json.get("sideboards", []):
#             return self._process_data(sideboards, template)
#         return []
#
#     @override
#     def _parse_input_for_decks_data(self) -> None:
#         self._deck_urls, self._container_urls = self._get_deck_urls(), self._get_sideboard_urls()
