"""

    mtg.deck.scrapers.pauperwave
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape Pauperwave decklists.

    @author: mazz3rr

"""
import json
import logging
from typing import Any, override

from mtg.deck.abc import DeckJsonParser
from mtg.deck.scrapers.abc import DecksJsonContainerScraper
from mtg.lib.json import Node
from mtg.lib.scrape.core import (
    ScrapingError, fetch_json, get_path_segments, is_more_than_root_path,
    strip_url_query,
)
from mtg.scryfall import Card

_log = logging.getLogger(__name__)


class PauperwaveDeckJsonParser(DeckJsonParser):
    """Parser of Pauperwave decklist JSON data.
    """
    @override
    def _parse_input_for_metadata(self) -> None:
        pass  # TODO
        # title_tag = self._deck_tag.previous_sibling
        # name, author, place = None, None, None
        # if " by " in title_tag.text:
        #     name, author = title_tag.text.split(" by ", maxsplit=1)
        #     if ", " in author:
        #         author, place = author.split(", ", maxsplit=1)
        # else:
        #     name = title_tag.text
        # self._metadata["name"] = name
        # if author:
        #     self._metadata["author"] = author
        # if place:
        #     self._metadata.setdefault("event", {})["place"] = place

    def _parse_card_data(self, card_data: dict) -> list[Card]:
        qty, name = card_data["quantity"], card_data["name"]
        return self.get_playset(self.find_card(name), qty)

    @override
    def _parse_input_for_decklist(self) -> None:
        for cat, card_list in self._deck_json.items():
            board = self._sideboard if cat == "Sideboard" else self._maindeck
            for card_data in card_list:
                if cat == "Commander":  # assumed case only (as I couldn't produce an example)
                    self._set_commander(self._parse_card_data(card_data)[0])
                else:
                    board += self._parse_card_data(card_data)
        self._metadata["format"] = "paupercommander" if self._commander else "pauper"


@DecksJsonContainerScraper.registered
class PauperwaveArticleScraper(DecksJsonContainerScraper):
    """Scraper of Pauperwave article page.
    """
    CONTAINER_NAME = "Pauperwave article"  # override
    DECK_JSON_PARSER_TYPE = PauperwaveDeckJsonParser # override
    JSON_FROM_SOUP = True  # override
    METADATA_BEFORE_DECKS = False
    _HOOK = "/_payload.json?"
    EXAMPLE_URLS = (
        "https://blog.pauperwave.org/articles/2026-04-26-pauperancino",  # decklist
        "https://blog.pauperwave.org/articles/2026-03-31-dennis-garbati-paupergeddon-spring-2026", # report
        "https://blog.pauperwave.org/articles/2025-12-06-tutorial-pingers",  # tutorial
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return is_more_than_root_path(url, "blog.pauperwave.org", "articles")

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = super().normalize_url(url)
        return strip_url_query(url)

    def _get_article_id(self) -> str:
        tag = self._soup.find("link", href=lambda h: h and self._HOOK in h)
        if not tag or not tag.attrs.get("href"):
            raise ScrapingError("No article ID tag", scraper=type(self), url=self.url)
        *_, article_id = tag["href"].split(self._HOOK)
        return article_id

    def _build_api_url(self) -> str:
        first, second, *_ = get_path_segments(self.url)
        return f"https://blog.pauperwave.org/{first}/{second}{self._HOOK}{self._get_article_id()}"

    @override
    def _extract_json(self) -> None:
        self._json = fetch_json(self._build_api_url())

    @staticmethod
    def _is_sentinel_item(item: Any) -> bool:
        return isinstance(item, dict) and "__hash__" in item

    @override
    def _validate_json(self) -> None:
        super()._validate_json()
        if not isinstance(self._json, list) and any(
            self._is_sentinel_item(item) for item in self._json):
            raise ScrapingError("Invalid JSON data", scraper=type(self), url=self.url)

    @override
    def _parse_input_for_metadata(self) -> None:
        pass  #  TODO
        # if event_tag := self._soup.find("p", class_="has-medium-font-size"):
        #     self._metadata["event"] = {}
        #     seen, key = set(), ""
        #     for el in event_tag.descendants:
        #         if el.name == "br":
        #             continue
        #         if el.text in seen:
        #             continue
        #         seen.add(el.text)
        #         if el.text.endswith(":"):
        #             key = el.text.lower().removesuffix(":")
        #         else:
        #             match key:
        #                 case "players":
        #                     self._metadata["event"][key] = int(el.text)
        #                 case "date":
        #                     with contextlib.suppress(ValueError):
        #                         self._metadata["event"][key] = parse_non_english_month_date(
        #                             el.text, *self._MONTHS)
        #                 case _:
        #                     self._metadata["event"][key] = el.text

    def _trim_data(self) -> None:
        """Trim the fetched JSON for it to hold only this-article-relevant data.
        """
        data = []
        sentinels = []
        for item in self._json:
            if len(sentinels) > 1:
                break
            if self._is_sentinel_item(item):
                sentinels.append(item)
            data.append(item)
        self._json = data

    @override
    def _parse_input_for_decks_data(self) -> None:
        self._trim_data()
        self._parse_input_for_metadata()
        node = Node(self._json)
        nodes = list(node.find_all(lambda n: n.is_str and "/cards.scryfall.io/" in n.data))
        self._decks_json = [json.loads(n.data) for n in nodes]
