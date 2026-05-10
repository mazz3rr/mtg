"""

    mtg.deck.scrapers.goldfish
    ~~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape MTGGoldfish decklists.

    @author: mazz3rr

"""
import logging
from typing import override

import dateutil.parser
from bs4 import Tag

from mtg.deck.abc import DeckTagParser
from mtg.deck.core import Deck, Mode
from mtg.deck.scrapers.abc import (
    DEFAULT_THROTTLING, DeckScraper, DeckUrlsContainerScraper, HybridContainerScraper,
)
from mtg.lib.common import ParsingError
from mtg.lib.numbers import extract_int
from mtg.lib.scrape.core import (
    ScrapingError, fetch_soup, http_requests_counted,
    strip_url_query, throttle,
)
from mtg.lib.scrape.dynamic import ConsentXpath, Xpath
from mtg.lib.time import timed
from mtg.scryfall import all_formats
from mtg.yt.discover import UrlHook

_log = logging.getLogger(__name__)


HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/96.0.4664.113 Safari/537.36}",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,"
              "image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9"
}
CONSENT_XPATH = "//button[@aria-label='Consent']"
URL_PREFIX = "https://www.mtggoldfish.com"
URL_HOOKS = (
    # regular deck
    UrlHook(
        ('"mtggoldfish.com/deck/"', ),
        ('-"/custom/"', ),
    ),
    # archetype deck
    UrlHook(
        ('"mtggoldfish.com/archetype/"', ),
        ('-"/custom/"', ),
    ),
    # tournament
    UrlHook(
        ('"mtggoldfish.com/tournament/"', ),
    ),
    # player
    UrlHook(
        ('"mtggoldfish.com/deck_searches/create?"', '"deck_search%5Bplayer%5D="'),
    ),
    # article & author
    UrlHook(
        ('"mtggoldfish.com/articles/"', ),
    ),
)


# alternative approach would be to scrape:
# self._soup.find("input", id="deck_input_deck").attrs["value"] which contains a decklist in
# Arena format (albeit with the need to .replace("sideboard", "Sideboard") or maybe some other
# safer means to achieve the same effect)
# yet another alternative approach would be to scrape:
# https://www.mtggoldfish.com/deck/arena_download/{DECK_ID} but this entails another request and
# parsing a DECK_ID from the first URL
class GoldfishDeckTagParser(DeckTagParser):
    """Parser of a MTGGoldfish decklist HTML tag.
    """
    def _parse_header_tag(self, header_tag: Tag) -> None:
        title_tag = header_tag.find("h1", class_="title")
        self._metadata["name"], *_ = title_tag.text.strip().split("\n")
        author_tag = title_tag.find("span")
        if author_tag is not None:
            self._metadata["author"] = author_tag.text.strip().removeprefix("by ")

    def _parse_info_tag(self, info_tag: Tag) -> None:
        lines = [l for l in info_tag.text.splitlines() if l]
        source_idx = None
        for i, line in enumerate(lines):
            if line.startswith("Format:"):
                fmt = line.removeprefix("Format:").strip().lower()
                self._update_fmt(fmt)
            elif line.startswith("Event:"):
                self._metadata["event"] = line.removeprefix("Event:").strip()
            elif line.startswith("Deck Source:"):
                source_idx = i + 1
            elif line.startswith("Deck Date:"):
                date_text = line.removeprefix("Deck Date:").strip()
                self._metadata["date"] = dateutil.parser.parse(date_text).date()
            elif line.startswith("Archetype:"):
                self._update_archetype_or_theme(line.removeprefix("Archetype:").strip())
        if source_idx is not None:
            self._metadata["original_source"] = lines[source_idx].strip()

    @override
    def _parse_input_for_metadata(self) -> None:
        header_tag = self._deck_tag.find("div", class_="header-container")
        self._parse_header_tag(header_tag)
        info_tag = self._deck_tag.find("p", class_="deck-container-information")
        self._parse_info_tag(info_tag)

    def _parse_decklist_tag(self, deck_tag: Tag) -> None:
        for tag in deck_tag.descendants:
            if tag.name == "tr" and tag.has_attr(
                    "class") and "deck-category-header" in tag.attrs["class"]:
                if "Sideboard" in tag.text:
                    self._state.shift_to_sideboard()
                elif "Commander" in tag.text:
                    self._state.shift_to_commander()
                elif "Companion" in tag.text:
                    self._state.shift_to_companion()
                elif not self._state.is_maindeck:
                    self._state.shift_to_maindeck()
            elif tag.name == "tr":
                td_tags = tag.find_all("td")
                if td_tags and len(td_tags) >= 3:
                    qty_tag, name_tag, *_ = td_tags
                    quantity = extract_int(qty_tag.text)
                    name = name_tag.text.strip()
                    cards = self.get_playset(self.find_card(name), quantity)
                    if self._state.is_maindeck:
                        self._maindeck += cards
                    elif self._state.is_sideboard:
                        self._sideboard += cards
                    elif self._state.is_commander:
                        self._set_commander(cards[0])
                    elif self._state.is_companion:
                        self._companion = cards[0]

    @override
    def _parse_input_for_decklist(self) -> None:
        decklist_tag = self._deck_tag.find("table", class_="deck-view-deck-table")
        if decklist_tag is None:
            raise ParsingError("Decklist tag not found")
        self._parse_decklist_tag(decklist_tag)


@DeckScraper.registered
class GoldfishDeckScraper(DeckScraper):
    """Scraper of MTGGoldfish decklist page.
    """
    SELENIUM_PARAMS = {  # override
        "xpaths": [
            Xpath("//table[@class='deck-view-deck-table']"),
        ],
        "consent_xpath": ConsentXpath(CONSENT_XPATH)
    }
    THROTTLING = DEFAULT_THROTTLING  # override
    EXAMPLE_URLS = (
        "https://www.mtggoldfish.com/deck/6833104#paper",
        "https://www.mtggoldfish.com/archetype/standard-mono-green-landfall-woe#paper"
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        url = url.lower()
        return (
            ("mtggoldfish.com/deck/" in url or "mtggoldfish.com/archetype/" in url)
            and "/custom/" not in url
        )

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = super().normalize_url(url)
        return strip_url_query(url).replace("/visual/", "/")

    # FIXME: this is never reached when faced with a Soft404 page as Selenium fails with timeout
    #  exception sooner than that (#378)
    @override
    def _is_soft_404_error(self) -> bool:
        tag = self._soup.find("h2")
        return tag and "Page not found" in tag.text

    @override
    def _get_sub_parser(self) -> GoldfishDeckTagParser:
        deck_tag = self._soup.find("div", class_="deck-container")
        if deck_tag is None:
            raise ScrapingError("Deck tag not found", scraper=type(self), url=self.url)
        return GoldfishDeckTagParser(deck_tag, self._metadata)

    @override
    def _parse_input_for_metadata(self) -> None:
        pass

    @override
    def _parse_input_for_decklist(self) -> None:
        pass


@DeckUrlsContainerScraper.registered
class GoldfishTournamentScraper(DeckUrlsContainerScraper):
    """Scraper of MTG Goldfish tournament page.
    """
    CONTAINER_NAME = "Goldfish tournament"  # override
    HEADERS = HEADERS  # override
    DECK_SCRAPER_TYPES = GoldfishDeckScraper,  # override
    DECK_URL_PREFIX = URL_PREFIX  # override
    EXAMPLE_URLS = (
        "https://www.mtggoldfish.com/tournament/pauper-league-2024-11-05#online",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return "mtggoldfish.com/tournament/" in url.lower()

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = super().normalize_url(url)
        return strip_url_query(url)

    @override
    def _parse_input_for_decks_data(self) -> None:
        table_tag = self._soup.find("table", class_="table-tournament")
        if not table_tag:
            raise ScrapingError("Tournament table tag not found", scraper=type(self), url=self.url)
        deck_tags = table_tag.find_all("a", href=lambda h: h and "/deck/" in h)
        self._deck_urls = [deck_tag.attrs["href"] for deck_tag in deck_tags]


@DeckUrlsContainerScraper.registered
class GoldfishPlayerScraper(DeckUrlsContainerScraper):
    """Scraper of MTG Goldfish player search page.
    """
    CONTAINER_NAME = "Goldfish player"  # override
    HEADERS = HEADERS  # override
    DECK_SCRAPER_TYPES = GoldfishDeckScraper,  # override
    DECK_URL_PREFIX = URL_PREFIX  # override
    EXAMPLE_URLS = (
        "https://www.mtggoldfish.com/deck_searches/create?utf8=%E2%9C%93&deck_search%5Bname%5D=&deck_search%5Bformat%5D=&deck_search%5Btypes%5D%5B%5D=&deck_search%5Btypes%5D%5B%5D=tournament&deck_search%5Btypes%5D%5B%5D=user&deck_search%5Bplayer%5D=Eliott_Dragon&deck_search%5Bdate_range%5D=01%2F26%2F2022+-+02%2F09%2F2022&deck_search%5Bdeck_search_card_filters_attributes%5D%5B0%5D%5Bcard%5D=&deck_search%5Bdeck_search_card_filters_attributes%5D%5B0%5D%5Bquantity%5D=1&deck_search%5Bdeck_search_card_filters_attributes%5D%5B0%5D%5Btype%5D=maindeck&deck_search%5Bdeck_search_card_filters_attributes%5D%5B1%5D%5Bcard%5D=&deck_search%5Bdeck_search_card_filters_attributes%5D%5B1%5D%5Bquantity%5D=1&deck_search%5Bdeck_search_card_filters_attributes%5D%5B1%5D%5Btype%5D=maindeck&counter=2&commit=Search",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return (
            "mtggoldfish.com/deck_searches/create?" in url.lower()
            and "deck_search%5Bplayer%5D=" in url
        )

    @override
    def _parse_input_for_decks_data(self) -> None:
        table_tag = self._soup.find("table", class_=lambda c: c and "table-striped" in c)
        if not table_tag:
            raise ScrapingError("<table> tag not found", scraper=type(self), url=self.url)
        deck_tags = table_tag.find_all("a", href=lambda h: h and "/deck/" in h)
        self._deck_urls = [deck_tag.attrs["href"] for deck_tag in deck_tags]


@HybridContainerScraper.registered
class GoldfishArticleScraper(HybridContainerScraper):
    """Scraper of MTG Goldfish article page.
    """
    SELENIUM_PARAMS = {  # override
        "xpaths": [
            Xpath("//div[@class='deck-container']", wait_for_all=True),
        ],
        "consent_xpath": ConsentXpath(CONSENT_XPATH),
    }
    CONTAINER_NAME = "Goldfish article"  # override
    DECK_TAG_PARSER_TYPE = GoldfishDeckTagParser  # override
    CONTAINER_SCRAPER_TYPES = GoldfishTournamentScraper,  # override
    EXAMPLE_URLS = (
        "https://www.mtggoldfish.com/articles/much-abrew-hammer-time-axe-time-modern",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return f"mtggoldfish.com/articles/" in url.lower() and "/search" not in url.lower()

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = super().normalize_url(url)
        return strip_url_query(url)

    def _collect_urls(self) -> tuple[list[str], list[str]]:
        main_tag = self._soup.find("div", class_="article-contents")
        if not main_tag:
            raise ScrapingError("Article tag not found", scraper=type(self), url=self.url)

        # filter out paragraphs that are covered by tag-based deck parser
        p_tags = [t for t in main_tag.find_all("p") if not t.find("div", class_="deck-container")]

        return self._find_links_in_tags(*p_tags)

    @override
    def _parse_input_for_decks_data(self) -> None:
        self._deck_tags = [*self._soup.find_all("div", class_="deck-container")]
        self._deck_urls, self._container_urls = self._collect_urls()


@HybridContainerScraper.registered
class GoldfishAuthorScraper(HybridContainerScraper):
    """Scraper of MTG Goldfish author page.
    """
    CONTAINER_NAME = "Goldfish author"  # override
    CONTAINER_SCRAPER_TYPES = GoldfishArticleScraper,  # override
    HEADERS = HEADERS  # override
    DECK_URL_PREFIX = URL_PREFIX  # override
    EXAMPLE_URLS = (
        "https://www.mtggoldfish.com/articles/search?author=93",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return "mtggoldfish.com/articles/search?" in url.lower() and "author=" in url

    @override
    def _parse_input_for_decks_data(self) -> None:
        container_tag = self._soup.select_one("div.articles-container")
        if not container_tag:
            raise ScrapingError("Container <div> tag not found", type(self), self.url)
        _, self._container_urls = self._find_links_in_tags(
            container_tag, url_prefix=self.DECK_URL_PREFIX)


@http_requests_counted("scraping meta decks")
@timed("scraping meta decks")
def scrape_meta(fmt="standard") -> list[Deck]:
    fmt = fmt.lower()
    if fmt not in all_formats():
        raise ValueError(f"Invalid format: {fmt!r}. Can be only one of: {all_formats()}")

    url = f"https://www.mtggoldfish.com/metagame/{fmt}/full"

    throttle(*(DEFAULT_THROTTLING * 2))
    soup = fetch_soup(url, headers=HEADERS)
    if not soup:
        raise ScrapingError(scraper=GoldfishDeckScraper, url=url)

    tiles = soup.find_all("div", class_="archetype-tile")
    if not tiles:
        raise ScrapingError("No deck tiles tags found", scraper=GoldfishDeckScraper, url=url)

    decks, metas = [], []
    for i, tile in enumerate(tiles, start=1):
        link = tile.find("a").attrs["href"]
        deck = GoldfishDeckScraper(
            f"https://www.mtggoldfish.com{link}", {"format": fmt}).scrape()
        count = tile.find("span", class_="archetype-tile-statistic-value-extra-data").text.strip()
        count = extract_int(count)
        metas.append({"place": i, "count": count})
        decks.append(deck)

    total = sum(m["count"] for m in metas)
    for deck, meta in zip(decks, metas):
        meta["share"] = meta["count"] * 100 / total
        deck.update_metadata(meta=meta)
        deck.update_metadata(mode=Mode.BO3.value)

    return decks
