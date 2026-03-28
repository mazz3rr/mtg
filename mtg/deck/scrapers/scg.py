"""

    mtg.deck.scrapers.scg
    ~~~~~~~~~~~~~~~~~~~~~
    Scrape StarCityGames decklists.

    @author: mazz3rr

"""
import json
import logging
from typing import override

import dateutil.parser
from bs4 import Tag

from mtg.constants import Json
from mtg.deck.abc import TagBasedDeckParser
from mtg.deck.scrapers.abc import DeckScraper, DeckUrlsContainerScraper, HybridContainerScraper
from mtg.lib.common import ParsingError, extract_int, from_iterable, sanitize_whitespace
from mtg.lib.scrape.core import ScrapingError, strip_url_query
from mtg.scryfall import COMMANDER_FORMATS

_log = logging.getLogger(__name__)


# the divide of deck scraping logic into tag-based scraper and URL-based scraper sprung from the
# perceived need of parsing StarCityGames decklists-containing articles (e.g.:
# https://articles.starcitygames.com/magic-the-gathering/the-coolest-rogue-decks-for-standard-at-magic-spotlight-foundations/
# with a tag-based scraper (that, incidentally, could share the same deck-extracting logic with the
# URL-based one). This turned out to be unnecessary as the decklist HTML tags in StarCityGames
# articles contain also decklist URLs so the old approach of parsing deck URLs for decks
# could be utilized.


class ScgDeckTagParser(TagBasedDeckParser):
    """Parser of a StarCityGames decklist page's HTML tag.
    """
    @staticmethod
    def _parse_event_line(line: str) -> Json | str:
        if " at " in line and " on " in line:
            data = {}
            place, rest = line.split(" at ", maxsplit=1)
            data["place"] = extract_int(place)
            event_name, date = rest.split(" on ", maxsplit=1)
            data["name"] = event_name
            data["date"] = dateutil.parser.parse(date.strip()).date()
            return data
        return line

    def _parse_header_tag(self, header_tag: Tag) -> None:
        self._metadata["name"] = header_tag.find("header", class_="deck_title").text.strip()
        self._metadata["author"] = header_tag.find("header", class_="player_name").text.strip()
        if event_tag := header_tag.find("header", class_="deck_played_placed"):
            event = sanitize_whitespace(event_tag.text.strip())
            self._metadata["event"] = self._parse_event_line(event)
        self._update_fmt(header_tag.find("div", class_="deck_format").text.strip().lower())

    @override
    def _parse_metadata(self) -> None:
        self._parse_header_tag(self._deck_tag.find("div", class_="deck_header"))

    def _parse_decklist_tag(self, decklist_tag: Tag) -> None:
        for tag in decklist_tag.descendants:
            if tag.name == "h3":
                if "Sideboard" in tag.text:
                    self._state.shift_to_sideboard()
                elif "Commander" in tag.text:
                    self._state.shift_to_commander()
                elif "Companion" in tag.text:
                    self._state.shift_to_companion()
                elif not self._state.is_maindeck:
                    self._state.shift_to_maindeck()
            elif tag.name == "li":
                name = tag.find("a").text.strip()
                quantity = int(tag.text.strip().removesuffix(name).strip())
                cards = self.get_playset(self.find_card(name), quantity)
                if self._state.is_maindeck:
                    self._maindeck += cards
                elif self._state.is_sideboard:
                    self._sideboard += cards
                elif self._state.is_commander:
                    self._set_commander(cards[0])
                elif self._state.is_companion:
                    self._companion = cards[0]
        if self.fmt in COMMANDER_FORMATS:
            deck_name = self._metadata["name"]
            if commander := from_iterable(self._maindeck, lambda c: c.name == deck_name):
                self._set_commander(commander)

    @override
    def _parse_deck(self) -> None:
        decklist_tag = self._deck_tag.find("div", class_="deck_card_wrapper")
        if decklist_tag is None:
            raise ParsingError("Decklist tag not found (page is probably paywalled)")
        self._parse_decklist_tag(decklist_tag)


@DeckScraper.registered
class ScgDeckScraper(DeckScraper):
    """Scraper of StarCityGames decklist page.
    """
    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        if "old.starcitygames.com/decks/" not in url.lower():
            return False
        url = url.removesuffix("/")
        _, end = url.split("/decks/", maxsplit=1)
        if all(ch.isdigit() for ch in end):
            return True
        return False

    @staticmethod
    @override
    def sanitize_url(url: str) -> str:
        return strip_url_query(url)

    @override
    def _get_sub_parser(self) -> ScgDeckTagParser:
        deck_tag = self._soup.find("div", class_="deck_listing")
        if deck_tag is None:
            deck_tag = self._soup.find("div", class_="deck_listing2")
            if deck_tag is None:
                raise ScrapingError("Deck tag not found", scraper=type(self), url=self.url)
        return ScgDeckTagParser(deck_tag, self._metadata)

    @override
    def _parse_metadata(self) -> None:
        pass

    @override
    def _parse_deck(self) -> None:
        pass


def _is_player_url(url: str) -> bool:
    tokens = "/p_first/", "/p_last/"
    part = "old.starcitygames.com/decks/results/"
    return part in url.lower() and any(t in url.lower() for t in tokens)


@DeckUrlsContainerScraper.registered
class ScgEventScraper(DeckUrlsContainerScraper):
    """Scraper of StarCityGames event page (or non-player deck search page).
    """
    CONTAINER_NAME = "StarCityGames event"  # override
    DECK_SCRAPERS = ScgDeckScraper,  # override

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        if "old.starcitygames.com/decks/" not in url.lower():
            return False
        url = url.removesuffix("/")
        _, end = url.split("/decks/", maxsplit=1)
        if "/" in end:
            if _is_player_url(url):
                return False
            # this will also catch arbitrary deck query results, e.g.:
            # https://old.starcitygames.com/decks/results/format/1-28-70/event_ID/49/[...]/start_num/0/
            return True
        return False

    @staticmethod
    @override
    def sanitize_url(url: str) -> str:
        return strip_url_query(url)

    @override
    def _collect(self) -> list[str]:
        section_tag = self._soup.select_one("section#content")
        if not section_tag:
            raise ScrapingError("Section tag not found", scraper=type(self), url=self.url)
        deck_tags = [
            a_tag for a_tag in section_tag.find_all(
                "a", href=lambda h: h and ScgDeckScraper.is_valid_url(h))]
        if not deck_tags:
            raise ScrapingError("Deck tags not found", scraper=type(self), url=self.url)
        return [tag.attrs["href"] for tag in deck_tags if tag is not None]


@DeckUrlsContainerScraper.registered
class ScgPlayerScraper(ScgEventScraper):
    """Scraper of StarCityGames player search page.
    """
    CONTAINER_NAME = "StarCityGames player"  # override

    @staticmethod
    def is_valid_url(url: str) -> bool:
        return _is_player_url(url)


@DeckUrlsContainerScraper.registered
class ScgDatabaseScraper(DeckUrlsContainerScraper):
    """Scraper of StarCityGames author's decks database page.
    """
    CONTAINER_NAME = "StarCityGames author's deck database"  # override
    DECK_SCRAPERS = ScgDeckScraper,  # override

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return "starcitygames.com/content/" in url.lower() and "-decks" in url.lower()

    @staticmethod
    @override
    def sanitize_url(url: str) -> str:
        return strip_url_query(url)

    @override
    def _collect(self) -> list[str]:
        db_div = self._soup.find("div", id="deck-database")
        if db_div is None:
            raise ScrapingError("Deck database tag not found", scraper=type(self), url=self.url)
        a_tags = [tag for tag in db_div.find_all("a", class_="dd-deck-link")]
        if not a_tags:
            raise ScrapingError("Deck tags not found", scraper=type(self), url=self.url)
        return [tag.attrs["href"].strip() for tag in a_tags]


class ScgArticleDeckTagParser(ScgDeckTagParser):
    """Parser of a StarCityGames article page's decklist HTML tag.
    """
    @override
    def _parse_decklist_tag(self, decklist_tag: Tag) -> str:
        decklist_text = decklist_tag.attrs.get("onclick")
        if not decklist_text:
            raise ParsingError("Text decklist missing from decklist tag's attributes")
        decklist_text = decklist_text.removeprefix("arenaExport(").removesuffix(")")
        decklist_data = json.loads(decklist_text)
        decklist = ["Deck", *[l for l in decklist_data["Maindeck"]]]
        if sideboard := decklist_data.get("Sideboard"):
            decklist += ["", "Sideboard", *[l for l in sideboard]]

        if self.fmt in COMMANDER_FORMATS:
            deck_name = self._metadata["name"]
            if commander_line := from_iterable(decklist, lambda l: deck_name in l):
                decklist.remove(commander_line)
                decklist = ["Commander", commander_line, "", *decklist]

        return "\n".join(decklist)

    @override
    def _parse_deck(self) -> None:
        css = "div[title='Export Decklist for Magic Arena'] > div"
        decklist_tag = self._deck_tag.select_one(css)
        if not decklist_tag:
            raise ParsingError("Decklist tag not found")
        self._decklist = self._parse_decklist_tag(decklist_tag)


@HybridContainerScraper.registered
class ScgArticleScraper(HybridContainerScraper):
    """Scraper of StarCityGames decks article page.
    """
    CONTAINER_NAME = "StarCityGames article"  # override
    TAG_BASED_DECK_PARSER = ScgArticleDeckTagParser  # override
    CONTAINER_SCRAPERS = ScgEventScraper,  # override

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return "articles.starcitygames.com/" in url.lower() and "/author/" not in url.lower()

    @staticmethod
    @override
    def sanitize_url(url: str) -> str:
        return strip_url_query(url)

    @override
    def _collect(self) -> tuple[list[str], list[Tag], list[Json], list[str]]:
        deck_tags = self._soup.find_all("div", class_="deck_listing")
        article_tag = self._soup.find("article", {"data-template": "post-content"})
        if article_tag is None:
            err = ScrapingError("Article tag not found", scraper=type(self), url=self.url)
            _log.warning(f"Scraping failed with: {err!r}")
            return [], deck_tags, [], []
        p_tags = [t for t in article_tag.find_all("p") if not t.find("div", class_="deck_listing")]
        deck_urls, article_urls = self._find_links_in_tags(*p_tags)
        return deck_urls, deck_tags, [], article_urls
