"""

    mtg.deck.scrapers.mtgrocks
    ~~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape MTGRocks articles for decklists.

    MTGRocks uses a third-party provider (MTGDecks.net) for its articles' decklists.

    @author: mazz3rr

"""
import json
import logging
from typing import override

import dateutil.parser

from mtg.deck.scrapers.abc import HybridContainerScraper
from mtg.lib.json import Node
from mtg.lib.scrape.core import ScrapingError, is_more_than_root_path, strip_url_query

_log = logging.getLogger(__name__)


@HybridContainerScraper.registered
class MtgRocksArticleScraper(HybridContainerScraper):
    """Scraper of MTGRocks article page.
    """
    CONTAINER_NAME = "MTGRocks article"  # override
    EXAMPLE_URLS = (
        "https://mtgrocks.com/dredgers-insight-mtg/",
    )

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        tokens = (
            "/mtg-arena-codes/", "/deck-builder/", "/category/", "/sitemap/", "/about-us/",
            "/editorial-policy/", "/privacy-policy/"
        )
        return (
            is_more_than_root_path(url, "mtgrocks.com")
            and not any(t in url.lower() for t in tokens)
        )

    @staticmethod
    @override
    def normalize_url(url: str) -> str:
        return strip_url_query(url)

    @override
    def _parse_input_for_metadata(self) -> None:
        script_tag = self._soup.find("script", type="application/ld+json")
        if not script_tag:
            raise ScrapingError("Metadata <script> tag not found", scraper=type(self), url=self.url)
        node = Node(json.loads(script_tag.text))
        if author_node := node.find(lambda n: "author" in n.data and "name" in n.data["author"]):
            self._metadata.setdefault("article", {})["author"] = author_node.data["author"]["name"]
        if title_node := node.find(lambda n: "headline" in n.data):
            self._metadata.setdefault("article", {})["title"] = title_node.data["headline"]
        if date_node := node.find(lambda n: "datePublished" in n.data):
            self._metadata.setdefault("article", {})["date"] = dateutil.parser.parse(
                date_node.data["datePublished"]).date()
        if desc_node := node.find(lambda n: "description" in n.data):
            self._metadata.setdefault("article", {})["description"] = desc_node.data["description"]
        if keywords_node := node.find(lambda n: "keywords" in n.data):
            self._metadata.setdefault(
                "article", {})["tags"] = self.normalize_metadata_deck_tags(
                keywords_node.data["keywords"])

    @override
    def _parse_input_for_decks_data(self) -> None:
        iframe_urls = [  # MTGDecks.net decklists
            tag.attrs["data-lazy-src"].removesuffix("/iframe")
            for tag in self._soup.find_all("iframe", {"data-lazy-src": lambda d: d})
        ]
        iframe_urls, _ = self._sift_links(*iframe_urls)

        article_tag = self._soup.select_one("div.elementor-widget-container")
        if not article_tag:
            err = ScrapingError("Article tag not found", scraper=type(self), url=self.url)
            _log.warning(f"Scraping failed with: {err!r}")
            self._deck_urls = iframe_urls
            return
        deck_urls, self._container_urls = self._find_links_in_tags(*article_tag.find_all("p"))
        self._deck_urls = sorted({*iframe_urls, *deck_urls}) if deck_urls else iframe_urls


@HybridContainerScraper.registered
class MtgRocksAuthorScraper(HybridContainerScraper):
    """Scraper of MTGRocks author page.
    """
    CONTAINER_NAME = "MTGRocks author"  # override
    CONTAINER_SCRAPER_TYPES = MtgRocksArticleScraper,  # override
    EXAMPLE_URLS = (
        "https://mtgrocks.com/author/zachary-fink/"
    )

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return "mtgrocks.com/author/" in url.lower()

    @override
    def _parse_input_for_decks_data(self) -> None:
        listing_tag = self._soup.select_one("div.jet-listing-grid")
        if not listing_tag:
            err = ScrapingError("Listing tag not found", scraper=type(self), url=self.url)
            _log.warning(f"Scraping failed with: {err!r}")
            return
        _, self._container_urls = self._find_links_in_tags(listing_tag)
