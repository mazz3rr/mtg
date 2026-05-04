"""

    mtg.deck.scrapers.deckstats
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape Deckstats decklists.

    @author: mazz3rr

"""
import itertools
import logging
from datetime import UTC, datetime
from typing import override

import backoff
from bs4 import BeautifulSoup
from requests import Response

from mtg.constants import Json
from mtg.deck.scrapers.abc import DeckScraper, DeckUrlsContainerScraper, video_throttled_deck_scraper
from mtg.lib.scrape.core import (
    ScrapingError, dissect_js, fetch, fetch_json, get_path_segments, strip_url_query,
    throttle,
)
from mtg.scryfall import Card
from mtg.yt.discover import UrlHook

_log = logging.getLogger(__name__)
URL_HOOKS = (
    # deck & users (user is: domain + user ID, and deck is: domain + user ID + deck ID)
    UrlHook(
        ('"deckstats.net/decks/"', ),
    ),
)
_FORMATS = {
    2: "vintage",
    3: "legacy",
    4: "modern",
    6: "standard",
    9: "pauper",
    10: "commander",
    15: "penny",
    16: "brawl",
    17: "oathbreaker",
    18: "pioneer",
    19: "historic",
    21: "duel",
    22: "explorer",
}
_MAX_TRIES = 3


def _backoff_predicate(response: Response) -> bool:
    if response.status_code == 429:
        msg = f"Request to TappedOut failed with: {response.status_code} {response.reason}"
        _log.warning(f"{msg}. Re-trying with backoff...")
        return True
    return False


def _backoff_handler(details: dict) -> None:
    _log.info("Backing off {wait:0.1f} seconds after {tries} tries...".format(**details))


@video_throttled_deck_scraper
@DeckScraper.registered
class DeckstatsDeckScraper(DeckScraper):
    """Scraper of Deckstats decklist page.
    """
    JSON_FROM_SOUP = True  # override
    EXAMPLE_URLS = (
        "https://deckstats.net/decks/231485/4286921-captain-n-ghathrod",
        "https://deckstats.net/decks/115134/1283677-kaya-s-scourge",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        if "deckstats.net/decks/" not in url.lower():
            return False
        try:
            segments = get_path_segments(url)
            _, user_id, deck_id_text = segments
            deck_id, *_ = deck_id_text.split("-")
            if all(ch.isdigit() for token in (user_id, deck_id) for ch in token):
                return True
            return False
        except ValueError:
            return False

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = super().normalize_url(url)
        return strip_url_query(url)

    @backoff.on_predicate(
        backoff.runtime,
        predicate=_backoff_predicate,
        value=lambda r: int(r.headers.get("Retry-After") or 60),
        jitter=None,
        max_tries=_MAX_TRIES,
        on_backoff=_backoff_handler,
    )
    def _get_response(self) -> Response | None:
        return fetch(self.url, handle_http_errors=False)

    @override
    def _is_page_inaccessible(self) -> bool:
        tag = self._soup.find("div", class_="ui-state-error")
        return tag and "You do not have access to this page." in tag.text

    @override
    def _is_soft_404_error(self) -> bool:
        tag = self._soup.find("div", class_="ui-state-error")
        return tag and "This deck does not exist." in tag.text

    @override
    def _extract_json(self) -> None:
        self._json = dissect_js(
            self._soup, "init_deck_data(", "deck_display();", lambda s: s.removesuffix(", false);"))

    @override
    def _pre_parse(self) -> None:
        response = self._get_response()
        if response.status_code == 429:
            raise ScrapingError(
                f"Page still not available after {_MAX_TRIES} backoff tries", scraper=type(self),
                url=self.url)
        self._soup = BeautifulSoup(response.text, "lxml")
        self._validate_soup()
        self._json = self._extract_json()

    @override
    def _parse_input_for_metadata(self) -> None:
        author_text = self._soup.find("div", id="deck_folder_subtitle").text.strip()
        self._metadata["author"] = author_text.removeprefix("in  ").removesuffix("'s Decks")
        self._metadata["name"] = self._json["name"]
        if views := self._json.get("views"):
            self._metadata["views"] = views
        if fmt_id := self._json.get("format_id"):
            if fmt := _FORMATS.get(fmt_id):
                self._update_fmt(fmt)
        self._metadata["date"] = datetime.fromtimestamp(self._json["updated"], UTC).date()
        if tags := self._json.get("tags"):
            self._metadata["tags"] = self.normalize_metadata_deck_tags(tags)
        if description := self._json.get("description"):
            self._metadata["description"] = description

    def _parse_card_json(self, card_json: Json) -> list[Card]:
        name = card_json["name"]
        quantity = card_json["amount"]
        if not card_json.get("data"):
            raise ScrapingError(
                f"No card data available for playset '{quantity} {name}'", scraper=type(self),
                url=self.url)
        if tcgplayer_id := card_json["data"].get("price_tcgplayer_id"):
            tcgplayer_id = int(tcgplayer_id)
        if mtgo_id := card_json["data"].get("price_cardhoarder_id"):
            mtgo_id = int(mtgo_id)
        card = self.find_card(name, tcgplayer_id=tcgplayer_id, mtgo_id=mtgo_id)
        if card_json.get("isCommander"):
            self._set_commander(card)
        return self.get_playset(card, quantity)

    @override
    def _parse_input_for_decklist(self) -> None:
        cards = itertools.chain(
            *[section["cards"] for section in self._json["sections"]])
        for card_json in cards:
            self._maindeck.extend(self._parse_card_json(card_json))
        if sideboard := self._json.get("sideboard"):
            for card_json in sideboard:
                self._sideboard.extend(self._parse_card_json(card_json))


@DeckUrlsContainerScraper.registered
class DeckstatsUserScraper(DeckUrlsContainerScraper):
    """Scraper of Deckstats user page.
    """
    THROTTLING = DeckUrlsContainerScraper.THROTTLING * 1.33  # override
    CONTAINER_NAME = "Deckstats user"  # override
    DECK_SCRAPER_TYPES = DeckstatsDeckScraper,  # override
    EXAMPLE_URLS = (
        "https://deckstats.net/decks/30513",
        "https://deckstats.net/decks/30513/?lng=it",
        "https://deckstats.net/decks/202938/?lng=fr",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        if "deckstats.net/decks/" not in url.lower():
            return False
        try:
            _, user_id = get_path_segments(url)
            if all(ch.isdigit() for ch in user_id):
                return True
            return False
        except ValueError:
            return False

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = super().normalize_url(url)
        return strip_url_query(url)

    def _get_user_id(self) -> str:
        *_, user_id = get_path_segments(self.url)
        return user_id

    @override
    def _parse_input_for_decks_data(self) -> None:
        api_url_template = (
            "https://deckstats.net/api.php?action=user_folder_get&result_type="
            "folder%3Bdecks%3Bparent_tree%3Bsubfolders&owner_id={}&folder_id=0&decks_page={}"
        )
        user_id = self._get_user_id()
        total, page = 1, 1
        last_seen = None
        while len(self._deck_urls) < total:
            if page != 1:
                throttle(*self.THROTTLING)
            json_data = fetch_json(api_url_template.format(user_id, page))
            if self._deck_urls and last_seen == json_data:
                break
            if not json_data or not json_data.get("folder") or not json_data["folder"].get("decks"):
                if not self._deck_urls:
                    err = ScrapingError("No decks data", scraper=type(self), url=self.url)
                    _log.warning(f"Scraping failed with: {err!r}")
                break
            total = json_data["folder"]["decks_total"]
            self._deck_urls += [f'https:{d["url_neutral"]}' for d in json_data["folder"]["decks"]]
            page += 1
            last_seen = json_data
