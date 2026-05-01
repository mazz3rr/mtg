"""

    mtg.deck.scrapers.penny
    ~~~~~~~~~~~~~~~~~~~~~~~
    Scrape PennyDreadfulMagic decklists.

    @author: mazz3rr

"""
import logging
from typing import override

from bs4 import Tag

from mtg.constants import Json
from mtg.deck.scrapers.abc import DeckScraper, DeckUrlsContainerScraper
from mtg.scryfall import Card
from mtg.lib.common import from_iterable
from mtg.lib.time import get_date_from_ago_text, get_date_from_month_text
from mtg.lib.scrape.core import (
    ScrapingError, fetch_json, fetch_soup, get_path_segments,
    strip_url_query,
)

_log = logging.getLogger(__name__)
URL_PREFIX = "https://pennydreadfulmagic.com"


@DeckScraper.registered
class PennyDreadfulMagicDeckScraper(DeckScraper):
    """Scraper of PennyDreadfulMagic decklist page.
    """
    EXAMPLE_URLS = (
        "https://pennydreadfulmagic.com/decks/245226/",
        "https://pennydreadfulmagic.com/decks/245223/",
    )

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return "pennydreadfulmagic.com/decks/" in url.lower()

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = super().normalize_url(url)
        return strip_url_query(url)

    @override
    def _parse_input_for_metadata(self) -> None:
        self._update_fmt("penny")
        self._metadata["name"] = self._soup.find("h1", class_="deck-name").text.strip()
        info_tag = self._soup.find("div", class_="title")
        if archetype_tag := info_tag.find("a", href=lambda h: h and "/archetypes/" in h):
            self._update_archetype_or_theme(archetype_tag.text.strip())
        author_tag = info_tag.find("a", href=lambda h: h and "/people/id/" in h)
        self._metadata["author"] = author_tag.text.strip()
        if date_tag := from_iterable(
            info_tag.find_all("div", class_="subtitle"), lambda t: not t.find("a")):
            date_text = date_tag.text.strip()
            if "ago" in date_text:
                self._metadata["date"] = get_date_from_ago_text(date_text)
            else:
                self._metadata["date"] = get_date_from_month_text(date_tag.text.strip())
        if event_tag := info_tag.find("a", href=lambda h: h and "/competitions/" in h):
            self._metadata["event"] = event_tag.text.strip()

    @classmethod
    def _parse_card_tag(cls, card_tag: Tag) -> list[Card]:
        text = card_tag.text.strip()
        qty_text, name = text.split(maxsplit=1)
        quantity = int(qty_text)
        return cls.get_playset(cls.find_card(name), quantity)

    @override
    def _parse_input_for_decklist(self) -> None:
        for section_tag in self._soup.find_all("section"):
            if section_tag.find("section"):  # skip higher-order sections
                continue
            h2_tag = section_tag.find("h2")
            if not h2_tag:  # skip irrelevant sections
                continue
            else:
                section = h2_tag.text.strip()
                card_tags = section_tag.find_all("a", class_="card")
                for card_tag in card_tags:
                    cards = self._parse_card_tag(card_tag)

                    if "Sideboard" in section:
                        self._sideboard += cards
                    else:
                        self._maindeck += cards


@DeckUrlsContainerScraper.registered
class PennyDreadfulMagicCompetitionScraper(DeckUrlsContainerScraper):
    """Scraper of PennyDreadfulMagic competition page.
    """
    CONTAINER_NAME = "PennyDreadfulMagic competition"  # override
    API_URL_TEMPLATE = (
        "https://pennydreadfulmagic.com/api/decks/?achievementKey=&archetypeId=&"
        "cardName=&competitionId={}&competitionFlagId=&deckType=&page=0&page"
        "Size=200&personId=&q=&seasonId="
    )  # override
    DECK_SCRAPER_TYPES = PennyDreadfulMagicDeckScraper,  # override
    DECK_URL_PREFIX = URL_PREFIX  # override
    EXAMPLE_URLS = (
        "https://pennydreadfulmagic.com/competitions/4764/",
    )

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        if "pennydreadfulmagic.com/competitions/" not in url.lower():
            return False
        try:
            _, competition_id = get_path_segments(url)
            if all(ch.isdigit() for ch in competition_id):
                return True
            return False
        except ValueError:
            return False

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = super().normalize_url(url)
        return strip_url_query(url)

    @override
    def _get_json_from_api(self) -> Json:
        *_, competition_id = get_path_segments(self.url)
        return fetch_json(self.API_URL_TEMPLATE.format(competition_id))

    @override
    def _validate_json(self) -> None:
        super()._validate_json()
        if not self._json.get("objects"):
            raise ScrapingError("No 'objects' data", scraper=type(self), url=self.url)

    @override
    def _parse_input_for_decks_data(self) -> None:
        self._deck_urls = [d["url"] for d in self._json["objects"]]


@DeckUrlsContainerScraper.registered
class PennyDreadfulMagicUserScraper(DeckUrlsContainerScraper):
    """Scraper of PennyDreadfulMagic user page.
    """
    CONTAINER_NAME = "PennyDreadfulMagic user"  # override
    API_URL_TEMPLATE = (
        "https://pennydreadfulmagic.com/api/decks/?achievementKey=&archetypeId="
        "&cardName=&competitionId=&competitionFlagId=&deckType=all&page=0&page"
        "Size=200&personId={}&q=&seasonId={}"
    )  # override
    DECK_SCRAPER_TYPES = PennyDreadfulMagicDeckScraper,  # override
    DECK_URL_PREFIX = URL_PREFIX  # override
    EXAMPLE_URLS = (
        "https://pennydreadfulmagic.com/seasons/33/people/id/2999/",
        "https://pennydreadfulmagic.com/people/id/2668/",
    )

    @property
    def _ids_in_url(self) -> bool:
        segments = set(get_path_segments(self.url.lower()))
        return "seasons" in segments and "id" in segments

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return "pennydreadfulmagic.com/" in url.lower() and "/people/" in url.lower()

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = super().normalize_url(url)
        return strip_url_query(url)

    @staticmethod
    def _parse_url_for_ids(url: str) -> tuple[str, str]:
        segments = get_path_segments(url.lower())
        seasons_idx, id_idx = segments.index("seasons"), segments.index("id")
        return segments[seasons_idx + 1], segments[id_idx + 1]

    def _find_ids(self) -> tuple[str, str] | None:
        soup = fetch_soup(self.url)
        if not soup:
            return None
        season_ids, user_ids = set(), set()
        for a_tag in soup.find_all(
                "a", href=lambda h: h and "/seasons/" in h and "/people/id/" in h):
            season_id, user_id = self._parse_url_for_ids(a_tag.attrs["href"])
            season_ids.add(int(season_id))
            user_ids.add(int(user_id))
        if not season_ids or not user_ids:
            return None
        return str(max(season_ids)), str(max(user_ids))

    def _get_ids(self) -> tuple[str, str] | None:
        if self._ids_in_url:
            return self._parse_url_for_ids(self.url)
        return self._find_ids()

    @override
    def _get_json_from_api(self) -> Json:
        return {}  # dummy

    @override
    def _validate_json(self) -> None:
        pass

    @override
    def _parse_input_for_decks_data(self) -> None:
        if ids := self._get_ids():
            season_id, user_id = ids
        else:
            raise ScrapingError(
                "Parameters needed for API missing from the URL", scraper=type(self), url=self.url)
        json_data = fetch_json(self.API_URL_TEMPLATE.format(user_id, season_id))
        if not json_data or not json_data.get("objects"):
            raise ScrapingError("No decks data", scraper=type(self), url=self.url)
        self._deck_urls = [d["url"] for d in json_data["objects"]]
