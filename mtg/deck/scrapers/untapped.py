"""

    mtg.deck.scrapers.untapped
    ~~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape Untapped.gg decklists.

    @author: mazz3rr

"""
import logging
from datetime import datetime
from typing import override

from mtg.deck.scrapers.abc import DEFAULT_THROTTLING, DeckScraper, DeckUrlsContainerScraper
from mtg.lib.numbers import extract_float, extract_int
from mtg.lib.scrape.core import ScrapingError, find_next_sibling_tag, normalize_url, strip_url_query
from mtg.lib.scrape.dynamic import ConsentXpath, Xpath

_log = logging.getLogger(__name__)
CONSENT_XPATH = '//button[contains(@class, "fc-button fc-cta-consent") and @aria-label="Consent"]'
CLIPBOARD_XPATH = "//span[text()='Copy to MTGA']"


@DeckScraper.registered
class UntappedProfileDeckScraper(DeckScraper):
    """Scraper of decklist page of Untapped.gg user's profile.
    """
    SELENIUM_PARAMS = {  # override
        "xpaths": [
            Xpath(
                text=CLIPBOARD_XPATH,
                halt_xpaths=(
                    "//div[text()='No games have been played with this deck in the selected time frame']",
                    "//div[text()='This profile is private']",
                )
            ),
        ],
    }
    EXAMPLE_URLS = (
        "https://mtga.untapped.gg/profile/1ebb0626-01d1-46e1-9de9-8fe0e44cf0bf/TYDMHO3B7ZBKVHKYYX34JLSSXA/deck/8fd8ce80-01a3-4df8-aafc-a0aa0f90969f?gameType=constructed&constructedType=ranked&constructedFormat=standard",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return "mtga.untapped.gg/profile/" in url.lower() and "/deck/" in url.lower()

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = normalize_url(url, case_sensitive=True)
        return strip_url_query(url)

    @override
    def _parse_input_for_metadata(self) -> None:
        name_tag = self._soup.select_one(
            "main > div > div > div > div > div > div > div > div > a > span > strong")
        self._metadata["name"] = name_tag.text.strip()
        author_tag = self._soup.find("h1", string=lambda s: s and s.endswith("'s Profile"))
        self._metadata["author"] = author_tag.text.strip().removesuffix("'s Profile")

    @override
    def _parse_input_for_decklist(self) -> None:
        self._decklist = self._clipboard


@DeckScraper.registered
class UntappedRegularDeckScraper(DeckScraper):
    """Scraper of a regular Untapped.gg decklist page.
    """
    SELENIUM_PARAMS = {  # override
        "xpaths": [
            Xpath(CLIPBOARD_XPATH),
        ],
        "consent_xpath": ConsentXpath(CONSENT_XPATH),
        "clipboard_xpath": CLIPBOARD_XPATH
    }
    EXAMPLE_URLS = (
        "https://mtga.untapped.gg/decks/AAQAAQmMBQHVBIy6JZiVBMC7BNixA7SDB_IDBpnSAe6FKNMBrf4D2PEK3QwJuQeH_yzxVgkDzPMDgIYErf8CVQPTmAGquDDegQQAAgikLsWWFJ2UFZ6kAePaAi-J7gOMgQcCv4AtoNAEAdMRAAADAr-ALchXAd_QMQHTEQAAAA",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return "mtga.untapped.gg/decks/" in url.lower()

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = normalize_url(url, case_sensitive=True)
        return strip_url_query(url)

    @override
    def _parse_input_for_metadata(self) -> None:
        name_tag = self._soup.select_one("main > div > div > div > h1")
        name = name_tag.text.strip()
        if " (" in name:
            name, *_ = name.split(" (")
        self._metadata["name"] = name

    @override
    def _parse_input_for_decklist(self) -> None:
        self._decklist = self._clipboard


@DeckScraper.registered
class UntappedMetaDeckScraper(DeckScraper):
    """Scraper of Untapped meta-decks page.
    """
    SELENIUM_PARAMS = {  # override
        "xpaths": [
            Xpath(CLIPBOARD_XPATH),
        ],
        "consent_xpath": ConsentXpath(CONSENT_XPATH),
        "clipboard_xpath": CLIPBOARD_XPATH
    }
    EXAMPLE_URLS = (
        "https://mtga.untapped.gg/constructed/standard/decks/304/mono-white-auras/AAQAAQAAAArnvwKJkSry7gS6Ch8EJ-nrDtDxAc4BARSIBQA",
        "https://mtga.untapped.gg/meta/decks/510/mono-red-aggro/AAQAAQPiqiH7mQQMAq7iAsOnHwAIstMCy9odZgu21wE2970DBAEV4gkA?tab=overview",
    )
    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        url = url.lower()
        return (
            "mtga.untapped.gg/meta/decks/" in url
            or ("mtga.untapped.gg/constructed/" in url and "decks" in url)
        )

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = normalize_url(url, case_sensitive=True)
        return strip_url_query(url)

    @override
    def _parse_input_for_metadata(self) -> None:
        name_tag = self._soup.find("h1")
        self._metadata["name"] = name_tag.text.strip().removesuffix(" Deck")
        if set_tag := self._soup.find("h2"):
            self._metadata["set"] = set_tag.text.strip()
        fmt_tag = self._soup.find("div", id="filter-format")
        self._metadata["format"] = fmt_tag.text.strip().lower()
        if time_tag := self._soup.find("time"):
            self._metadata["date"] = datetime.strptime(
                time_tag.attrs["datetime"], "%Y-%m-%dT%H:%M:%S.%fZ").date()
        # info
        info_tag = name_tag.parent
        info_tag = find_next_sibling_tag(info_tag)
        info_tag = [*info_tag][0]
        try:
            winrate, matches, avg_duration = info_tag
            self._metadata["meta"] = {}
            if winrate.text.strip():
                self._metadata["meta"]["winrate"] = extract_float(winrate.text.strip())
            if matches.text.strip():
                self._metadata["meta"]["matches"] = extract_int(matches.text.strip())
            if avg_duration.text.strip():
                self._metadata["meta"]["avg_minutes"] = extract_float(avg_duration.text.strip())
        except ValueError as e:
            if not "unpack" in str(e):
                raise
        # time range
        i_tag = self._soup.select_one("#filter-time-range > div > div > div > i")
        time_range_tag = i_tag.parent
        self._metadata.setdefault(
            "meta", {})["time_range_since"] = time_range_tag.text.removesuffix("Now")

    @override
    def _parse_input_for_decklist(self) -> None:
        self._decklist = self._clipboard


@DeckUrlsContainerScraper.registered
class UntappedProfileScraper(DeckUrlsContainerScraper):
    """Scraper of Untapped.gg user profile page.
    """
    SELENIUM_PARAMS = {  # override
        "xpaths": [
            Xpath(
                text="//a[contains(@href, '/profile/') and contains(@class, 'deckbox')]",
                wait_for_all=True,
            ),
        ],
        "consent_xpath": ConsentXpath(CONSENT_XPATH),
    }
    THROTTLING = DEFAULT_THROTTLING * 1.4  # override
    CONTAINER_NAME = "Untapped profile"  # override
    DECK_SCRAPER_TYPES = UntappedProfileDeckScraper,  # override
    DECK_URL_PREFIX = "https://mtga.untapped.gg"  # override
    EXAMPLE_URLS = (
        "https://mtga.untapped.gg/profile/390de354-4ae6-4ea5-9991-2f650825ba18/8D5B7E0B33092E80",
        "https://mtga.untapped.gg/profile/d2b05c8d-0b0b-45c7-b108-f520cb235225/C237D55E2E6FD774",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return "mtga.untapped.gg/profile/" in url.lower() and "/deck/" not in url.lower()

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = normalize_url(url, case_sensitive=True)
        return strip_url_query(url)

    @override
    def _parse_input_for_decks_data(self) -> None:
        a_tags = self._soup.find_all("a", href=lambda h: h and "/profile/" in h)
        a_tags = [a_tag for a_tag in a_tags if "deckbox" in a_tag.attrs["class"]]
        if not a_tags:
            raise ScrapingError("Deck tags not found", scraper=type(self), url=self.url)
        self._deck_urls = [a_tag["href"] for a_tag in a_tags]
