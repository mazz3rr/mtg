"""

    mtg.deck.scrapers.cardsrealm
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape Cardsrealm decklists.

    @author: mazz3rr

"""
import logging
import time
from typing import override

import dateutil.parser
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common import ElementClickInterceptedException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from mtg.constants import Json
from mtg.deck.scrapers.abc import (
    DeckScraper, DeckUrlsContainerScraper, HybridContainerScraper,
    folder_container_scraper,
)
from mtg.lib.scrape.core import ScrapingError, dissect_js, get_path_segments, strip_url_query
from mtg.lib.scrape.dynamic import ConsentXpath, DEFAULT_TIMEOUT, accept_consent
from mtg.lib.time import timed
from mtg.yt.discover import UrlHook

_log = logging.getLogger(__name__)
NEGATIVE_DOMAINS = (
    "board.cardsrealm.com/",
    "hs.cardsrealm.com/",
    "lol.cardsrealm.com"
    "lor.cardsrealm.com/",
    "lorcana.cardsrealm.com/",
    "onepiece.cardsrealm.com/",
    "pokemon.cardsrealm.com/",
    "yugioh.cardsrealm.com/",
)
URL_PREFIX = "https://mtg.cardsrealm.com"
URL_HOOKS = (
    # deck & profile & folder
    UrlHook(
        ('"cardsrealm.com/"', '"/decks"'),
    ),
    # meta-deck tournament
    UrlHook(
        ('"cardsrealm.com/"', '"/tournaments/"'),
    ),
    # regular tournament
    UrlHook(
        ('"cardsrealm.com/"', '"/tournament/"'),
    ),
    # article & author & article searches
    UrlHook(
        ('"cardsrealm.com/"', '"/articles/"'),
        tuple(f'-"{t}"' for t in NEGATIVE_DOMAINS),
    ),
)


def to_eng_url(url: str, first_non_lang_segment: str) -> str:
    segments = get_path_segments(url)
    if not segments:
        raise ValueError(f"Cannot parse path segments from {url!r}")
    if segments[0] == first_non_lang_segment:
        return url  # no lang code in url (implicitly means 'en-us')
    lang = segments[0]
    return url.replace(f"/{lang}/", "/en-us/")


# there's an alternative approach of using the API, e.g.:
# https://mtg.cardsrealm.com/en-us/app/getDeckById?deck_id=357787
@DeckScraper.registered
class CardsrealmDeckScraper(DeckScraper):
    """Scraper of Cardsrealm decklist page.
    """
    JSON_FROM_SOUP = True  # override
    EXAMPLE_URLS = (
        "https://mtg.cardsrealm.com/en-us/decks/io11-bg-harvest?utm_source=monarchs&utm_medium=affiliate&utm_campaign=podcast",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        positives = ("cardsrealm.com/", "/decks/")
        negatives = ("/profile/", "/folder/")
        return (
            all(p in url.lower() for p in positives)
            and not any(n in url.lower() for n in negatives)
        )

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = super().normalize_url(url)
        url = strip_url_query(url)
        return to_eng_url(url, "decks")

    @override
    def _extract_json(self) -> None:
        def process(text: str) -> str:
            obj, _ = text.rsplit("]", maxsplit=1)
            return obj + "]"
        self._json = dissect_js(
            self._soup, "var deck_cards = ", 'var torneio_type =', end_processor=process)

    @override
    def _parse_input_for_metadata(self) -> None:
        card_data = self._json[0]
        self._metadata["name"] = card_data["deck_title"]
        date_text = card_data.get("deck_lastchange", card_data['deck_card_datetime'])
        self._metadata["date"] = dateutil.parser.parse(date_text).date()
        self._metadata["author"] = card_data["givenNameUser"]
        if views := card_data.get("deck_views"):
            self._metadata["views"] = views
        self._update_fmt(card_data["tour_type_name"].lower())

    def _parse_card_json(self, card_json: Json) -> None:
        name = card_json["name_of_card"]
        quantity = card_json["deck_quantity"]
        card = self.find_card(name)
        if card_json["deck_sideboard"] == 1:
            self._sideboard += self.get_playset(card, quantity)
        elif card_json["deck_sideboard"] == 0:
            self._maindeck += self.get_playset(card, quantity)

    @override
    def _parse_input_for_decklist(self) -> None:
        # filter out tokens and maybe-boards
        for card_data in [cd for cd  in self._json if cd["deck_sideboard"] in (0, 1)]:
            self._parse_card_json(card_data)
        self._derive_commander_from_sideboard()


@DeckUrlsContainerScraper.registered
class CardsrealmProfileScraper(DeckUrlsContainerScraper):
    """Scraper of Cardsrealm user profile page.
    """
    CONTAINER_NAME = "Cardsrealm profile"  # override
    DECK_SCRAPER_TYPES = CardsrealmDeckScraper,  # override
    DECK_URL_PREFIX = URL_PREFIX  # override
    EXAMPLE_URLS = (
        "https://mtg.cardsrealm.com/en-us/profile/mateus-queiroz-n35/decks",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        positives = ("cardsrealm.com/", "/profile/", "/decks")
        negatives = ("/folder/", )
        return (
            all(p in url.lower() for p in positives)
            and not any(n in url.lower() for n in negatives)
        )

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = super().normalize_url(url)
        url = strip_url_query(url)
        return to_eng_url(url, "profile")

    @override
    def _parse_input_for_decks_data(self) -> None:
        deck_divs = [
            div for div in self._soup.find_all("div", class_=lambda c: c and "deck_div_all" in c)
        ]
        deck_tags = [d.find("a", href=lambda h: h and "/decks/" in h) for d in deck_divs]
        if not deck_tags:
            raise ScrapingError("Deck tags not found", scraper=type(self), url=self.url)
        self._deck_urls = [tag.attrs["href"] for tag in deck_tags]


@folder_container_scraper
@DeckUrlsContainerScraper.registered
class CardsrealmFolderScraper(CardsrealmProfileScraper):
    """Scraper of Cardsrealm decks folder page.
    """
    CONTAINER_NAME = "Cardsrealm folder"  # override
    EXAMPLE_URLS = (
        "https://mtg.cardsrealm.com/en-us/decks/folder/1l7-pauper",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        positives = ("cardsrealm.com/", "/decks/folder/")
        negatives = ("/profile/", )
        return (
            all(p in url.lower() for p in positives)
            and not any(n in url.lower() for n in negatives)
        )

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        return CardsrealmDeckScraper.normalize_url(url)


@DeckUrlsContainerScraper.registered
class CardsrealmMetaTournamentScraper(DeckUrlsContainerScraper):
    """Scraper of Cardsrealm meta-deck tournaments page.
    """
    CONTAINER_NAME = "Cardsrealm meta-deck tournament"  # override
    DECK_SCRAPER_TYPES = CardsrealmDeckScraper,  # override
    DECK_URL_PREFIX = URL_PREFIX  # override
    EXAMPLE_URLS = (
        "https://mtg.cardsrealm.com/en-us/meta-decks/pauper/tournaments/1k27j-pauper-royale-220",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return all(t in url.lower() for t in ("cardsrealm.com/", "/meta-decks/", "/tournaments/"))

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = super().normalize_url(url)
        url = strip_url_query(url)
        return to_eng_url(url, "meta-decks")

    @override
    def _parse_input_for_decks_data(self) -> None:
        deck_tags = [
            tag for tag in
            self._soup.find_all(
                lambda t: t.name == "a"
                          and t.text.strip() == "Decklist"
                          and t.parent.name == "span"
            )
        ]
        if not deck_tags:
            raise ScrapingError("Deck tags not found", scraper=type(self), url=self.url)
        self._deck_urls = sorted({tag.attrs["href"] for tag in deck_tags})


@DeckUrlsContainerScraper.registered
class CardsrealmRegularTournamentScraper(DeckUrlsContainerScraper):
    """Scraper of Cardsrealm regular tournaments page.
    """
    CONTAINER_NAME = "Cardsrealm regular tournament"  # override
    DECK_SCRAPER_TYPES = CardsrealmDeckScraper,  # override
    EXAMPLE_URLS = (
        "https://mtg.cardsrealm.com/en-us/tournament/1k27j-pauper-royale-220",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return (
            all(t in url.lower() for t in ("cardsrealm.com/", "/tournament/"))
            and "/meta-decks/" not in url.lower()
        )

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = super().normalize_url(url)
        url = strip_url_query(url)
        return to_eng_url(url, "tournament")

    @timed("fetching dynamic soup")
    def _fetch_dynamic_soup(self) -> BeautifulSoup:
        with webdriver.Chrome() as driver:
            try:
                _log.info(f"Webdriving using Chrome to: '{self.url}'...")
                driver.get(self.url)

                consent_xpath = ConsentXpath('//p[text()="Consent"]')
                accept_consent(driver, consent_xpath)

                xpath = "//button[text()='show deck']"
                buttons = WebDriverWait(driver, DEFAULT_TIMEOUT).until(
                    EC.presence_of_all_elements_located((By.XPATH, xpath)))
                _log.info("Page has been loaded and XPath-specified elements are present")

                for btn in buttons:
                    btn.click()

                time.sleep(0.5)
                return BeautifulSoup(driver.page_source, "lxml")

            except ElementClickInterceptedException:
                msg = "Selenium click intercepted by a pop-up. Not all decklists gathered"
                err = ScrapingError(msg, scraper=type(self), url=self.url)
                _log.warning(f"Scraping failed with: {err!r}")
                return BeautifulSoup(driver.page_source, "lxml")

            except TimeoutException:
                err = ScrapingError(self._selenium_timeout_msg, scraper=type(self), url=self.url)
                _log.warning(f"Scraping failed with: {err!r}")
                return BeautifulSoup(driver.page_source, "lxml")

    @override
    def _fetch_soup(self) -> None:
        self._soup = self._fetch_dynamic_soup()

    @override
    def _parse_input_for_decks_data(self) -> None:
        deck_divs = [
            div for div in self._soup.find_all("div", class_=lambda c: c and "mainDeck" in c)]
        deck_tags = [d.find("a", href=lambda h: h and "/decks/" in h) for d in deck_divs]
        if not deck_tags:
            raise ScrapingError("Deck tags not found", scraper=type(self), url=self.url)
        self._deck_urls = [tag.attrs["href"] for tag in deck_tags if tag is not None]


@HybridContainerScraper.registered
class CardsrealmArticleScraper(HybridContainerScraper):
    """Scraper of Cardsrealm decks article page.
    """
    CONTAINER_NAME = "Cardsrealm article"  # override
    # override
    CONTAINER_SCRAPER_TYPES = (
        CardsrealmProfileScraper, CardsrealmFolderScraper, CardsrealmRegularTournamentScraper,
        CardsrealmMetaTournamentScraper)
    EXAMPLE_URLS = (
        "https://mtg.cardsrealm.com/en-us/articles/explorer-mono-blue-tempo-deck-tech-sideboard-guide",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        positives = ("cardsrealm.com/", "/articles/")
        negatives = ("/search/", "/author/", *NEGATIVE_DOMAINS)
        return (
            all(p in url.lower() for p in positives)
            and not any(n in url.lower() for n in negatives)
        )

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = super().normalize_url(url)
        url = strip_url_query(url)
        return to_eng_url(url, "articles")

    @override
    def _parse_input_for_decks_data(self) -> None:
        article_tag = self._soup.find("div", id="article_div_all")
        if article_tag is None:
            raise ScrapingError("Article tag not found", scraper=type(self), url=self.url)
        self._deck_urls, self._container_urls = self._find_links_in_tags(
            article_tag, url_prefix=URL_PREFIX)


@HybridContainerScraper.registered
class CardsrealmAuthorScraper(HybridContainerScraper):
    """Scraper of Cardsrealm article's author page.
    """
    CONTAINER_NAME = "Cardsrealm author"  # override
    CONTAINER_SCRAPER_TYPES = CardsrealmArticleScraper,  # override
    EXAMPLE_URLS = (
        "https://cardsrealm.com/en-us/articles/author/humberto2151",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        positives = ("cardsrealm.com/", "/articles/author/")
        negatives = ("/search/", *NEGATIVE_DOMAINS)
        return (
            all(p in url.lower() for p in positives)
            and not any(n in url.lower() for n in negatives)
        )

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        return CardsrealmArticleScraper.normalize_url(url)

    @override
    def _parse_input_for_decks_data(self) -> None:
        _, self._container_urls = self._find_links_in_tags(self._soup)


@HybridContainerScraper.registered
class CardsrealmArticleSearchScraper(HybridContainerScraper):
    """Scraper of Cardsrealm article search page.
    """
    CONTAINER_NAME = "Cardsrealm article search"  # override
    CONTAINER_SCRAPER_TYPES = CardsrealmArticleScraper,  # override
    EXAMPLE_URLS = (
        "https://cardsrealm.com/en-us/articles/search/?keyword=humberto2151",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        positives = ("cardsrealm.com/", "/articles/search/", "keyword=")
        return (
            all(p in url.lower() for p in positives)
            and not any(n in url.lower() for n in NEGATIVE_DOMAINS)
        )

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        return CardsrealmArticleScraper.normalize_url(url)

    @override
    def _parse_input_for_decks_data(self) -> None:
        main_tag = self._soup.select_one("div#articlePage")
        if not main_tag:
            raise ScrapingError("Main <div> tag not found", type(self), self.url)
        _, self._container_urls = self._find_links_in_tags(main_tag)
