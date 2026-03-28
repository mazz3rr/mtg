"""

    mtg.deck.scrapers.topdecked
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape TopDecked decklists.

    @author: mazz3rr

"""
import contextlib
import logging
from typing import override

import backoff
import dateutil.parser
from selenium import webdriver
from selenium.common import NoSuchElementException
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from mtg.deck.arena import IllFormedArenaDecklist, PlaysetLine
from mtg.deck.core import CardNotFound, Deck, InvalidDeck
from mtg.deck.scrapers.abc import DeckScraper
from mtg.lib.common import ParsingError, extract_float, get_date_from_ago_text
from mtg.lib.scrape.core import ScrapingError, strip_url_query
from mtg.lib.scrape.dynamic import SELENIUM_TIMEOUT, click_for_clipboard
from mtg.scryfall import COMMANDER_FORMATS

_log = logging.getLogger(__name__)


def _sanitize_element_text(text: str) -> str:
    text = text.strip()
    if "\n" in text:
        text, *_ = text.split("\n")
    return text


# FIXME: get data from the API (#310)
@DeckScraper.registered
class TopDeckedRegularDeckScraper(DeckScraper):
    """Scraper of TopDecked regular decklist page.
    """
    CONSENT_XPATH = "//ion-button[contains(., 'Ok!')]"
    CONSENT_TIMEOUT = 30
    SHARE_XPATH = "//ion-button[@tourstep='decklist_view_share']"
    ARENA_XPATH = "//button[descendant::*[contains(text(), 'Share to Arena')]]"
    ARENA_DELAY = 5
    NAME_XPATH = "//ion-title[contains(@class, 'title-default')]"
    FMT_XPATH = "//span[contains(@class, 'format') and contains(@class, 'text-uppercase')]"
    DATE_XPATH = ("//span[contains(@class, 'prefix') and contains(@class, 'text-none') and "
                  "contains(@class, 'ng-star-inserted')]")

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return "www.topdecked.com/decks/" in url.lower()

    @staticmethod
    @override
    def sanitize_url(url: str) -> str:
        return strip_url_query(url)

    def _process_metadata_with_selenium(self, driver: webdriver.Chrome) -> None:
        name_el = driver.find_element(By.XPATH, self.NAME_XPATH)
        self._metadata["name"] = _sanitize_element_text(name_el.text)
        fmt_el = driver.find_element(By.XPATH, self.FMT_XPATH)
        self._update_fmt(_sanitize_element_text(fmt_el.text))
        with contextlib.suppress(NoSuchElementException):  # meta-decks feature no date data
            date_el = driver.find_element(By.XPATH, self.DATE_XPATH)
            date_text = _sanitize_element_text(date_el.text)
            if "ago" in date_text:
                self._metadata["date"] = get_date_from_ago_text(date_text)
            else:
                self._metadata["date"] = dateutil.parser.parse(date_text).date()

    def _get_data(self) -> str:
        with webdriver.Chrome() as driver:
            _log.info(f"Webdriving using Chrome to: '{self.url}'...")
            driver.get(self.url)

            # consent
            consent = WebDriverWait(driver, self.CONSENT_TIMEOUT).until(
                EC.element_to_be_clickable((By.XPATH, self.CONSENT_XPATH)))
            consent.click()
            WebDriverWait(driver, SELENIUM_TIMEOUT / 2).until_not(
                EC.presence_of_element_located((By.XPATH, self.CONSENT_XPATH)))
            _log.info("Consent pop-up closed")

            # metadata
            self._process_metadata_with_selenium(driver)

            # arena decklist
            share_btn = WebDriverWait(driver, SELENIUM_TIMEOUT).until(
                EC.element_to_be_clickable((By.XPATH, self.SHARE_XPATH)))
            share_btn.click()
            _log.info("Share button clicked")
            return click_for_clipboard(driver, self.ARENA_XPATH, self.ARENA_DELAY)

    # _get_data() does all the work here
    @override
    def _pre_parse(self) -> None:
        try:
            self._decklist = self._get_data()
        except NoSuchElementException as err:
            err_text, *_ = str(err).split("(Session info")
            raise ScrapingError(
                f"Scraping failed due to: '{err_text.strip()}'", scraper=type(self), url=self.url)
        except TimeoutException:
            raise ScrapingError(
                f"Scraping failed due to Selenium timing out", scraper=type(self), url=self.url)

    @override
    def _parse_metadata(self) -> None:
        pass

    def _handle_commander(self) -> None:
        decklist = self._decklist.splitlines()
        commander_line, partner_line = decklist[1:3]
        commander = PlaysetLine(commander_line).to_playset()[0]
        partner = PlaysetLine(partner_line).to_playset()[0]
        if commander.is_partner and partner.is_partner:
            del decklist[1:3]
            decklist.insert(0, "Commander")
            decklist.insert(1, commander_line)
            decklist.insert(2, partner_line)
            decklist.insert(3, "")
        else:
            del decklist[1]
            decklist.insert(0, "Commander")
            decklist.insert(1, commander_line)
            decklist.insert(2, "")
        self._decklist = "\n".join(decklist)

    @override
    def _parse_deck(self) -> None:
        if self.fmt and self.fmt in COMMANDER_FORMATS:
            self._handle_commander()

    @backoff.on_exception(
        backoff.expo, IllFormedArenaDecklist, max_time=60)
    @override
    def scrape(
            self, throttled=False, suppressed_errors=(ParsingError, ScrapingError)) -> Deck | None:
        try:
            return super().scrape(throttled, suppressed_errors=())
        except IllFormedArenaDecklist:
            raise
        except suppressed_errors as err:
            if isinstance(err, ParsingError) and not isinstance(err, (InvalidDeck, CardNotFound)):
                err = ScrapingError(str(err), type(self), self.url)
            _log.warning(f"Scraping failed with: {err!r}")
            return None


@DeckScraper.registered
class TopDeckedMetaDeckScraper(TopDeckedRegularDeckScraper):
    """Scarper of TopDecked meta-deck decklist page.
    """
    SHARE_XPATH = "//ion-button[contains(text(), 'Share')]"  # override
    _META_SHARE_XPATH = (
        "//span[contains(@class, 'text-medium') and contains(@class, 'subtext') "
        "and contains(text(), 'of meta')]")

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return "www.topdecked.com/metagame/" in url.lower() and "/decks/" in url.lower()

    @override
    def _process_metadata_with_selenium(self, driver: webdriver.Chrome) -> None:
        super()._process_metadata_with_selenium(driver)
        meta_share_el = driver.find_element(By.XPATH, self._META_SHARE_XPATH)
        self._metadata["meta_share"] = extract_float(_sanitize_element_text(meta_share_el.text))
