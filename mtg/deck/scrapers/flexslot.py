"""

    mtg.deck.scrapers.flexslot
    ~~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape Flexslot.gg decklists.

    @author: mazz3rr

"""
import json
import logging
from typing import override

import dateutil.parser

from mtg.deck.scrapers.abc import (
    DeckScraper, HybridContainerScraper,
)
from mtg.lib.json import Node, node_from_njs_fd_markup
from mtg.lib.scrape.core import (
    ScrapingError, extract_url, is_more_than_root_path, strip_url_query,
)
from mtg.lib.scrape.dynamic import Xpath, TIMEOUT
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
        "xpaths": [
            Xpath(
                text="//script[contains(text(), 'api.scryfall.com/cards/')]",
                halt_xpaths=(
                    "//div[contains(text(), 'Exclusive Content') "
                    "or contains(text(), 'Subscriber Exclusive') "
                    "or contains(text(), 'Paid Exclusive') "
                    "or contains(text(), 'Content Not Found')]",
                    "//h3[contains(text(), 'No Cards in Deck')]",
                )
            ),
        ],
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
        "xpaths": [
            Xpath("//div[contains(concat(' ', normalize-space(@class), ' '), ' ProseMirror ')]"),
        ],
    }
    PARSE_NJS_FD = True
    CONTAINER_NAME = "Flexslot article"  # override
    EXAMPLE_URLS = (
        "https://flexslot.gg/articles/851e3727-03c3-4f9c-8ba6-261e7da1a869",
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
            self._metadata["article"]["tags"] = self.normalize_metadata_deck_tags(tags)
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
        links = [link for link in [extract_url(l) for l in article_text.splitlines()] if link]
        links += [n.data for n in data_node.find_all(lambda n: n.key == "href")]
        self._deck_urls, self._container_urls = self._sift_links(*links)


@HybridContainerScraper.registered
class FlexslotUserScraper(HybridContainerScraper):
    """Scraper of Flexslot user page.
    """
    SELENIUM_PARAMS = {  # override
        "xpaths": [
            Xpath(
                text="//a[contains(@href, '/decks/')]",
                wait_for_all=True,
                timeout=TIMEOUT / 4,
            ),
            Xpath(
                text = "//a[contains(@href, '/sideboards/')]",
                wait_for_all=True,
                timeout=TIMEOUT / 4,
            ),
            Xpath(
                text="//a[contains(@href, '/articles/')]",
                wait_for_all=True,
                timeout=TIMEOUT / 4,
            ),
        ],
    }
    CONTAINER_NAME = "Flexslot user"  # override
    DECK_SCRAPER_TYPES = FlexslotDeckScraper, FlexslotSideboardDeckScraper  # override
    CONTAINER_SCRAPER_TYPES = FlexslotArticleScraper,  #override
    EXAMPLE_URLS = (
        "https://flexslot.gg/u/YungDingo",
    )

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
    def _parse_input_for_decks_data(self) -> None:
        deck_tags = self._soup.find_all("a", href=lambda h: h and "/decks/" in h)
        sideboards_tags = self._soup.find_all("a", href=lambda h: h and "/sideboards/" in h)
        articles_tags = self._soup.find_all("a", href=lambda h: h and "/articles/" in h)
        self._deck_urls, self._container_urls = self._find_links_in_tags(
            *[*deck_tags, *sideboards_tags, *articles_tags], url_prefix="https://flexslot.gg")
