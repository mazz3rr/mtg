"""

    mtg.deck.scrapers.tappedout
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape TappedOut decklists.

    @author: mazz3rr

"""
import logging
from datetime import datetime
from typing import override

import backoff
from bs4 import BeautifulSoup
from requests import Response

from mtg.constants import SECRETS
from mtg.deck.scrapers.abc import (
    DeckScraper, DeckUrlsContainerScraper, DEFAULT_THROTTLING, folder_container_scraper,
)
from mtg.lib.numbers import extract_int
from mtg.lib.scrape.core import (
    ScrapingError, fetch, fetch_json, fetch_soup, get_path_segments, prepend_url,
    strip_url_query, throttle,
)
from mtg.lib.time import get_date_from_ago_text

_log = logging.getLogger(__name__)
URL_PREFIX = "https://tappedout.net"
_MAX_TRIES = 6
_THROTTLING = DEFAULT_THROTTLING * 6
HEADERS = {
    "Host": "tappedout.net",
    "User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:150.0) Gecko/20100101 Firefox/150.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Sec-GPC": "1",
    "Connection": "keep-alive",
    "Cookie": SECRETS["tappedout"]["cookie"],
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "If-Modified-Since": "Sun, 21 Sep 2025 01:16:30 GMT",
    "Priority": "u=0, i",
    "TE": "trailers",
}


def _backoff_predicate(response: Response) -> bool:
    if response.status_code == 429:
        msg = f"Request to TappedOut failed with: {response.status_code} {response.reason}"
        _log.warning(f"{msg}. Re-trying with backoff...")
        return True
    return False


def _backoff_handler(details: dict) -> None:
    _log.info("Backing off {wait:0.1f} seconds after {tries} tries...".format(**details))


@DeckScraper.registered
class TappedoutDeckScraper(DeckScraper):
    """Scraper of TappedOut decklist page.
    """
    THROTTLING = _THROTTLING  # override
    EXAMPLE_URLS = (
        "https://tappedout.net/mtg-decks/rakdos-orc-sac/?cb=1687559977",
        "https://tappedout.net/mtg-decks/pias-myr-deck/",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return "tappedout.net/mtg-decks/" in url.lower()

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
        return fetch(self.url, handle_http_errors=False, headers=HEADERS)

    @override
    def _fetch_soup(self) -> None:
        response = self._get_response()
        if response.status_code == 429:
            raise ScrapingError(
                f"Page still not available after {_MAX_TRIES} backoff tries", scraper=type(self),
                url=self.url)
        self._soup = BeautifulSoup(response.text, "lxml")

    @override
    def _is_soft_404_error(self) -> bool:
        return "Page not found (404)" in self._soup.text

    @override
    def _parse_input_for_metadata(self) -> None:
        fmt_tag = self._soup.select_one("a.btn.btn-success.btn-xs")
        if fmt_tag is None:
            raise ScrapingError(f"Format tag not found", scraper=type(self), url=self.url)
        fmt = fmt_tag.text.strip().removesuffix("*").lower()
        self._update_fmt(fmt)
        self._metadata["author"] = self._soup.select_one('a[href*="/users/"]').text.strip()
        deck_details_table = self._soup.find("table", id="deck-details")
        for row in deck_details_table.find_all("tr"):
            cols = row.find_all("td")
            if len(cols) != 2:
                continue
            name_col, value_col = cols
            if name_col.text.strip() == "Last updated":
                date_text = value_col.text.strip()
                if date_text == "a few seconds":
                    self._metadata["date"] = datetime.today()
                else:
                    self._metadata["date"] = get_date_from_ago_text(value_col.text.strip())
            elif name_col.text.strip() == "Views":
                if views := value_col.text.strip():
                    self._metadata["views"] = extract_int(views)

    @override
    def _parse_input_for_decklist(self) -> None:
        decklist_tag = self._soup.find("textarea", id="mtga-textarea")
        if not decklist_tag:
            raise ScrapingError("Decklist tag not found", scraper=type(self), url=self.url)
        lines = decklist_tag.text.strip().splitlines()
        _, name_line, _, _, *lines = lines
        self._decklist = "\n".join(lines)
        self._metadata["name"] = name_line.removeprefix("Name ")


@DeckUrlsContainerScraper.registered
class TappedoutUserScraper(DeckUrlsContainerScraper):
    """Scraper of Tappedout user page.
    """
    CONTAINER_NAME = "Tappedout user"  # override
    # override
    DECK_SCRAPER_TYPES = TappedoutDeckScraper,  # override
    EXAMPLE_URLS = (
        "https://tappedout.net/users/ChuckWagonMTG/",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        if "tappedout.net/users/" not in url.lower():
            return False
        try:
            _, user_name = get_path_segments(url)
            return True
        except ValueError:
            return False

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = super().normalize_url(url)
        return strip_url_query(url)

    def _get_user_name(self) -> str:
        _, user_name = get_path_segments(self.url)
        return user_name

    @override
    def _parse_input_for_decks_data(self) -> None:
        api_url_template = "https://tappedout.net/api/users/{}/deck-list/?p={}&o=-date_updated"
        username = self._get_user_name()
        collected, total, page = [], 1, 1
        while len(collected) < total:
            if page != 1:
                throttle(*_THROTTLING)
            json_data = fetch_json(api_url_template.format(username, page))
            if not json_data or not json_data.get("results") or not json_data.get("total_decks"):
                if not collected:
                    err = ScrapingError("No decks data", scraper=type(self), url=self.url)
                    _log.warning(f"Scraping failed with: {err!r}")
                break
            total = json_data["total_decks"]
            collected += [prepend_url(result["url"], URL_PREFIX) for result in json_data["results"]]
            page += 1
        if not collected:
            raise ScrapingError("Decks not found", scraper=type(self), url=self.url)
        self._deck_urls = collected


@folder_container_scraper
@DeckUrlsContainerScraper.registered
class TappedoutFolderScraper(DeckUrlsContainerScraper):
    """Scraper of Tappedout folder page.
    """
    CONTAINER_NAME = "Tappedout folder"  # override
    JSON_FROM_API = True  # override
    DECK_SCRAPER_TYPES = TappedoutDeckScraper,  # override
    DECK_URL_PREFIX = URL_PREFIX  # override
    EXAMPLE_URLS = (
        "https://tappedout.net/mtg-deck-folders/commanders-of-myr-edh-decks/",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return "tappedout.net/mtg-deck-folders/" in url.lower()

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = super().normalize_url(url)
        return strip_url_query(url)

    def _get_folder_id(self) -> int:
        soup = fetch_soup(self.url)
        start_hook, end_hook = "window.django = ", ";"
        script_tag = soup.find("script", string=lambda s: s and start_hook in s and end_hook in s)
        text = script_tag.text
        *_, first = text.split(start_hook)
        second, *_ = first.split(end_hook)
        *_, third = second.split("folderId: ")
        return extract_int(third)

    @override
    def _fetch_json(self) -> None:
        api_url = f"https://tappedout.net/api/folder/{self._get_folder_id()}/detail/"
        self._json = fetch_json(api_url)

    @override
    def _validate_json(self) -> None:
        super()._validate_json()
        if not self._json.get("folder") or not self._json["folder"].get("decks"):
            raise ScrapingError("No decks data", scraper=type(self), url=self.url)

    @override
    def _parse_input_for_decks_data(self) -> None:
        self._deck_urls = [d["url"] for d in self._json["folder"]["decks"]]


@folder_container_scraper
@DeckUrlsContainerScraper.registered
class TappedoutUserFolderScraper(TappedoutUserScraper):
    """Scraper of Tappedout user folders page.
    """
    CONTAINER_NAME = "Tappedout user folder"  # override
    EXAMPLE_URLS = (
        "https://tappedout.net/users/PDHPals/deck-folders/",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return "tappedout.net/users/" in url.lower() and "/deck-folders" in url.lower()

    @override
    def _get_user_name(self) -> str:
        _, user_name, *_ = get_path_segments(self.url)
        return user_name

    @override
    def _parse_input_for_decks_data(self) -> None:
        api_url_template = "https://tappedout.net/api/folder/{}/list/?page_num={}"
        username = self._get_user_name()
        collected, has_next, page = [], True, 1
        while has_next:
            if page != 1:
                throttle(*_THROTTLING)
            json_data = fetch_json(api_url_template.format(username, page))
            if not json_data or not json_data.get("results"):
                if not collected:
                    err = ScrapingError("No decks data", scraper=type(self), url=self.url)
                    _log.warning(f"Scraping failed with: {err!r}")
                break
            has_next = json_data.get("hasNext", False)
            collected += [
                prepend_url(d["url"], URL_PREFIX) for folder in json_data["results"]
                for d in folder["decks"]]
            page += 1
        if not collected:
            raise ScrapingError("Decks not found", scraper=type(self), url=self.url)
        self._deck_urls = sorted(set(collected))
