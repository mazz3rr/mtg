"""

    mtg.deck.scrapers.mtgcircle
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape MTGCircle decklists.

    @author: mazz3rr

"""
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Type, override

from bs4 import BeautifulSoup, Tag

from mtg.constants import Json
from mtg.deck.abc import JsonBasedDeckParser
from mtg.deck.scrapers.abc import DeckScraper, HybridContainerScraper
from mtg.lib.common import from_iterable
from mtg.lib.json import Node
from mtg.lib.scrape.core import ScrapingError, dissect_js
from mtg.scryfall import Card, all_formats

_log = logging.getLogger(__name__)
_THROTTLING = DeckScraper.THROTTLING * 2


class MtgCircleDeckJsonParser(JsonBasedDeckParser):
    """Parser of MTGCircle deck JSON data.
    """
    def _parse_metadata(self) -> None:
        if fmt := self._deck_data.get("format"):
            self._update_fmt(fmt)
        arch = self._deck_data.get("archetypeName")
        if arch:
            self._update_archetype_or_theme(arch)
        if name := self._deck_data.get("deckName"):
            self._metadata["name"] = name
        elif arch:
            self._metadata["name"] = arch
        if author := self._deck_data.get("deckAuthorName"):
            self._metadata["author"] = author
        if "gamesWon" in self._deck_data and self._deck_data["gamesWon"] is not None:
            self._metadata["games_won"] = self._deck_data["gamesWon"]
        if "gamesLost" in self._deck_data and self._deck_data["gamesLost"] is not None:
            self._metadata["games_lost"] = self._deck_data["gamesLost"]
        if winrate := self._deck_data.get("winrate"):
            self._metadata["winrate"] = winrate
        if source := self._deck_data.get("source"):
            self._metadata["original_source"] = source
        if date := self._deck_data.get("date"):
            self._metadata["date"] = datetime.fromtimestamp(date / 1000, UTC).date()
        if arena_fmt := self._deck_data.get("eventName"):
            self._metadata.setdefault("arena", {})["format"] = arena_fmt
        if arena_rank := self._deck_data.get("rank"):
            self._metadata.setdefault("arena", {})["rank"] = arena_rank
        if arena_mode := self._deck_data.get("gameMode"):
            self._metadata.setdefault("arena", {})["mode"] = arena_mode

    def _parse_card(self, card_json: Json) -> list[Card]:
        name = card_json["name"]
        qty = card_json["quantity"]
        setcode, collector_number = card_json.get("set", ""), card_json.get("collector_number", "")
        set_and_number = (setcode, collector_number) if setcode and collector_number else None
        oracle_id = card_json.get("oracle_id", "")
        return self.get_playset(self.find_card(
            name, set_and_collector_number=set_and_number, oracle_id=oracle_id), qty)

    @override
    def _parse_deck(self) -> None:
        for card_json in self._deck_data["cards"]:
            match card_json["deckPos"]:
                case "mainDeck":
                    self._maindeck += self._parse_card(card_json)
                case "sideboard":
                    self._sideboard += self._parse_card(card_json)
                case "commander":
                    self._set_commander(self._parse_card(card_json)[0])
                case "companion":
                    self._companion = self._parse_card(card_json)[0]
                case _:
                    pass


def get_data(
        soup: BeautifulSoup, scraper: Type[DeckScraper] | Type[HybridContainerScraper], url: str,
        retriever: Callable[[Json], Json], start_pos=3) -> Json:
    tokens = "cards", "deckPos", "mainDeck", "name", "quantity"
    script_tag = soup.find("script", string=lambda s: s and all(t in s for t in tokens))
    if not script_tag:
        raise ScrapingError("Data <script> tag not found", scraper=scraper, url=url)
    try:
        js_data = dissect_js(
            script_tag, 'self.__next_f.push(', '|||dummy|||', end_processor=lambda t: t[:-1])
        data = json.loads(js_data[1][start_pos:])
        return retriever(data)
    except (AttributeError, KeyError) as err:
        raise ScrapingError(
            "Failed data extraction from <script> tag's JavaScript",
            scraper=scraper, url=url) from err


# FIXME: #443
# @throttled_deck_scraper
# @DeckScraper.registered
class MtgCircleVideoDeckScraper(DeckScraper):
    """Scraper of MTGCircle video decklist page.
    """
    DATA_FROM_SOUP = True  # override
    THROTTLING = _THROTTLING * 2  # override
    SELENIUM_PARAMS = {  # override
        "xpath": "//script[contains(text(), 'cards') and contains(text(), 'deckPos') and "
                 "contains(text(), 'mainDeck') and contains(text(), 'name') and contains(text(), "
                 "'quantity')]",
        "headers": {"Referer": "https://www.youtube.com/"}  # FIXME: try passing a cookie (#443)
    }

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        if "mtgcircle.com/videos/" not in url.lower() or "/draft/" in url.lower():
            return False
        *_, rest = url.lower().split("mtgcircle.com/videos/")
        if "/" not in rest:
            return False
        return True

    # FIXME: this doesn't work as it's in a part loaded dynamically
    @override
    def _is_soft_404_error(self) -> bool:
        texts = "Oops! Something went wrong on our end", "This page does not exist"
        if self._soup.find("h1", string=lambda s: s and s in texts):
            return True
        return False

    def _retrieve_deck_data(self, data: Json) -> Json:
        node = Node(data)
        decks = [*node.find_all(
            lambda n: "deck" in n.name and isinstance(n.data, dict) and "cards" in n.data)]
        if not decks:
            raise ScrapingError("Decks data not found", scraper=type(self), url=self.url)
        # sort to return the deck with the most cards (not an opponent's one)
        decks.sort(key=lambda n: sum(card["quantity"] for card in n.data["cards"]))
        return decks[-1].data

    @override
    def _get_data_from_soup(self) -> Json:
        return get_data(self._soup, type(self), self.url, self._retrieve_deck_data)

    @override
    def _get_sub_parser(self) -> MtgCircleDeckJsonParser:
        return MtgCircleDeckJsonParser(self._data, self._metadata)

    @override
    def _parse_metadata(self) -> None:
        fmt_tags = self._soup.select("nav > ol > li > a")
        if fmt_tag := from_iterable(
                fmt_tags,
                lambda t: "#" not in t.attrs["href"] and t.text != "Videos"
                          and t.text.lower() in all_formats()):
            self._update_fmt(fmt_tag.text)

    @override
    def _parse_deck(self) -> None:
        pass


# FIXME: #443
# @throttled_deck_scraper
# @DeckScraper.registered
class MtgCircleRegularDeckScraper(MtgCircleVideoDeckScraper):
    """Scraper of MTGCircle regular decklist page.
    """
    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        if "mtgcircle.com/decks/" not in url.lower() or "/draft/" in url.lower():
            return False
        *_, rest = url.lower().split("mtgcircle.com/decks/")
        if "/" not in rest:
            return False
        return True

    @override
    def _retrieve_deck_data(self, data: Json) -> Json:
        node = Node(data)
        deck = node.find(
            lambda n: isinstance(n.data, dict) and "cards" in n.data
                      and "variations" in n.parent.name )
        if deck is None:
            raise ScrapingError("Deck data not found", scraper=type(self), url=self.url)
        return deck.data


# FIXME: #443
# @HybridContainerScraper.registered
class MtgCircleArticleScraper(HybridContainerScraper):
    """Scraper of MTGCircle article page.
    """
    CONTAINER_NAME = "MTGCircle article"  # override
    JSON_BASED_DECK_PARSER = MtgCircleDeckJsonParser  # override
    THROTTLING = _THROTTLING  # override
    SELENIUM_PARAMS = MtgCircleVideoDeckScraper.SELENIUM_PARAMS  # override

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return "mtgcircle.com/articles/" in url.lower()

    def _retrieve_date_data(self, data: Json) -> Json:
        node = Node(data)
        dates = [*node.find_all(
            lambda n: isinstance(n.data, dict) and "date" in n.data and "userContent" in n.data)]
        if not dates:
            raise ScrapingError("Date data not found", scraper=type(self), url=self.url)
        # sort to not return an update date (the most recent) instead of creation date
        dates.sort(key=lambda n: n.data["date"])
        return dates[0].data

    @override
    def _parse_metadata(self) -> None:
        headline_tag = self._soup.select_one("div.container-custom > div > h1")
        self._metadata["title"] = headline_tag.text.strip()
        css = "div.container-custom > div > span > a > li.items-center.bg-paper"
        fmt_tag, *info_tags = self._soup.select(css)
        self._update_fmt(fmt_tag.text.strip())
        self._metadata["article_tags"] = [t.text.strip().lower() for t in info_tags]
        date_data = get_data(
            self._soup, type(self), self.url, retriever=self._retrieve_date_data, start_pos=2)
        self._metadata["date"] = datetime.fromtimestamp(date_data["date"] / 1000, UTC).date()

    @staticmethod
    def _retrieve_decks_data(data: Json) -> list[Json]:
        node = Node(data)
        return [d.data for d in node.find_all(
            lambda n: isinstance(n.data, dict) and "cards" in n.data and "deckId" in n.data)]

    @override
    def _collect(self) -> tuple[list[str], list[Tag], list[Json], list[str]]:
        decks_data = get_data(
            self._soup, type(self), self.url, retriever=self._retrieve_decks_data, start_pos=2)
        article_tag = self._soup.find("article")
        if not article_tag:
            err = ScrapingError("Article tag not found", scraper=type(self), url=self.url)
            _log.warning(f"Scraping failed with: {err!r}")
            return [], [], decks_data, []
        deck_urls, container_urls = self._find_links_in_tags(*article_tag.find_all("p"))
        return deck_urls, [], decks_data, container_urls
