"""

    mtg.deck.scrapers.aetherhub
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape Aetherhub decklists.

    @author: mazz3rr

"""
import logging
from datetime import datetime
from typing import override

import dateutil.parser
from bs4 import Tag

from mtg.constants import Json
from mtg.deck.core import Mode
from mtg.deck.abc import TagBasedDeckParser
from mtg.deck.scrapers.abc import DeckScraper, DeckUrlsContainerScraper, HybridContainerScraper, \
    throttled_deck_scraper
from mtg.lib.common import extract_float, extract_int, from_iterable
from mtg.lib.scrape.core import ScrapingError, strip_url_query
from mtg.yt.discover import UrlHook

_log = logging.getLogger(__name__)
URL_PREFIX = "https://aetherhub.com"
URL_HOOKS = (
    # regular deck
    UrlHook(
        ('"aetherhub.com/"', '"/deck/"'),
        ('-"export/"', '-"/mydecks"', '-"/builder"'),
    ),
    # write-up deck
    UrlHook(
        ('"aetherhub.com/decks/writeups/"', ),
    ),
    # user
    UrlHook(
        ('"aetherhub.com/user/"', ),
    ),
    # event
    UrlHook(
        ("aetherhub.com/events/", ),
        limit=100
    ),
    # article
    UrlHook(
        ('"aetherhub.com/article/"', ),
    ),
)
CONSENT_XPATH = '//button[@class="ncmp__btn" and contains(text(), "Accept")]'


# TODO: scrape the meta


class AetherhubDeckTagParser(TagBasedDeckParser):
    """Parser of an Aetherhub decklist HTML tag.
    """
    @override
    def _parse_metadata(self) -> None:
        pass

    @override
    def _parse_deck(self) -> None:
        for tag in self._deck_tag.descendants:
            if tag.name in ("h4", "h5"):
                if "Side" in tag.text:
                    self._state.shift_to_sideboard()
                elif "Commander" in tag.text:
                    self._state.shift_to_commander()
                elif "Companion" in tag.text:
                    self._state.shift_to_companion()
                elif not self._state.is_maindeck:
                    self._state.shift_to_maindeck()
            elif tag.name == "tr":
                td_tags = tag.find_all("td")
                if td_tags:
                    td_tag = td_tags[0]
                    lines = [l.strip() for l in td_tag.text.split("\n") if l.strip()]
                    if len(lines) < 2:
                        continue  # not a <td> card tag
                    qty_text, name = lines
                    quantity = int(qty_text)
                    card_tag = td_tag.find("a")
                    set_code = card_tag.attrs.get("data-card-set").lower()
                    collector = card_tag.attrs.get("data-card-number")
                    set_col = (set_code, collector) if set_code and collector else None
                    cards = self.get_playset(self.find_card(name, set_col), quantity)
                    if self._state.is_maindeck:
                        self._maindeck += cards
                    elif self._state.is_sideboard:
                        self._sideboard += cards
                    elif self._state.is_commander:
                        self._set_commander(cards[0])
                    elif self._state.is_companion:
                        self._companion = cards[0]


@throttled_deck_scraper
@DeckScraper.registered
class AetherhubDeckScraper(DeckScraper):
    """Scraper of Aetherhub decklist page.

    Note:
        Companions are part of a sideboard list and aren't listed separately.
    """
    _FORMATS = {
        "Arena Standard": ("standard", Mode.BO1),
        "Standard": ("standard", Mode.BO3),
        "Alchemy": ("alchemy", Mode.BO1),
        "Traditional Alchemy": ("alchemy", Mode.BO3),
        "Historic": ("historic", Mode.BO1),
        "Traditional Historic": ("historic", Mode.BO3),
        "Explorer": ("explorer", Mode.BO1),
        "Traditional Explorer": ("explorer", Mode.BO3),
        "Timeless": ("timeless", Mode.BO1),
        "Traditional Timeless": ("timeless", Mode.BO3),
        "Brawl": ("standardbrawl", Mode.BO3),
        "Historic Brawl": ("brawl", Mode.BO3),
        "Pioneer": ("pioneer", Mode.BO3),
        "Modern": ("modern", Mode.BO3),
        "Legacy": ("legacy", Mode.BO3),
        "Vintage": ("vintage", Mode.BO3),
        "Commander": ("commander", Mode.BO3),
        "Oathbreaker": ("oathbreaker", Mode.BO3),
    }
    SELENIUM_PARAMS = {  # override
        "xpath": '//div[@class="row"]',
        "wait_for_all": True,
        "consent_xpath": CONSENT_XPATH,
        "wait_for_consent_disappearance": False
    }

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        url = url.lower()
        tokens = "export/", "/mydecks", "/builder"
        return ("aetherhub.com/" in url and "/deck/" in url
                and not any(t in url.lower() for t in tokens))

    @staticmethod
    @override
    def sanitize_url(url: str) -> str:
        url = strip_url_query(url)
        if "/gallery/" in url.lower():
            url = url.replace("/Gallery/", "/Public/").replace("/gallery/", "/public/")
        elif url.lower().endswith("/gallery"):
            url = url.removesuffix("/Gallery").removesuffix("/gallery")
        return url.removesuffix("/")

    @override
    def _is_page_inaccessible(self) -> bool:
        return "Welcome Back" in self._soup.text

    @override
    def _is_soft_404_error(self) -> bool:
        return "404 Page Not Found" in self._soup.text

    @override
    def _get_sub_parser(self) -> AetherhubDeckTagParser:
        deck_tags = self._soup.find_all("div", class_="row")
        deck_tag = from_iterable(
            deck_tags, lambda t: t.text.strip().startswith(("Main", "Commander", "Companion")))
        if deck_tag is None:
            raise ScrapingError("Deck tag not found", scraper=type(self), url=self.url)
        return AetherhubDeckTagParser(deck_tag, self._metadata)

    @override
    def _parse_metadata(self) -> None:
        # name and format
        if title_tag := self._soup.find(["h2", "h3"], class_="text-header-gold"):
            fmt_part, name_part = title_tag.text.strip().split("-", maxsplit=1)
            fmt_part = fmt_part.strip()
            if fmt_pair := self._FORMATS.get(fmt_part):
                fmt, mode = fmt_pair
                self._update_fmt(fmt)
                self._metadata["mode"] = mode.value
            self._metadata["name"] = name_part.strip()

        # author (only in user-submitted decklists)
        if author_tag := self._soup.find('a', href=lambda href: href and "/User/" in href):
            self._metadata["author"] = author_tag.text.strip().removesuffix("'s profile")

        # archetype
        if archetype_tag := self._soup.find(["div", "span"], class_="archetype-tag"):
            archetype = archetype_tag.text.strip().lower()
            self._update_archetype_or_theme(archetype)

        # date and other (only in user-submitted decklists)
        if date_tags := self._soup.select(
                "div.col-xs-7.col-sm-7.col-md-7.col-lg-7.pl-0.pr-0.text-left"):
            date_tag = date_tags[0]
            date_lines = [l.strip() for l in date_tag.text.strip().splitlines() if l]
            date_text = date_lines[0].removeprefix("Last Updated: ").strip()
            self._metadata["date"] = datetime.strptime(date_text, "%d %b %Y").date()
            self._metadata["views"] = int(date_lines[2])
            self._metadata["exports"] = int(date_lines[3])
            self._metadata["comments"] = int(date_lines[4])

        # meta (only in meta decklists)
        if meta_tag := self._soup.find("h4", class_="text-center"):
            _, text = meta_tag.text.strip().split(" - ")
            count_text, share_text = text.strip().split(", ")
            self._metadata["meta"] = {}
            self._metadata["meta"]["count"] = extract_int(count_text)
            self._metadata["meta"]["share"] = extract_float(share_text)

        # event (only in event decklists)
        if event_tag := self._soup.find("h5", class_="text-center"):
            text = event_tag.text.strip()
            if "\nby " in text and "/" in text:
                self._metadata["event"] = {}
                rank, *rest = event_tag.text.strip().split(maxsplit=1)
                if any(rank.endswith(t) for t in ("st", "nd", "rd", "th")):
                    self._metadata["event"]["rank"] = rank
                    rest = " ".join(rest)
                else:
                    rest = rank + " " + " ".join(rest)
                *rest, author = rest.rsplit("\nby ", maxsplit=1)
                self._metadata["event"]["player"] = self._metadata["author"] = author
                rest = " ".join(rest)
                *rest, date = rest.rsplit(maxsplit=1)
                if "/" in date:
                    self._metadata["event"]["date"] = dateutil.parser.parse(date).date()
                else:
                    rest = date + " " + " ".join(rest)
                rest = " ".join(rest)
                self._metadata["event"]["name"] = rest

    @override
    def _parse_deck(self) -> None:
        pass


@DeckScraper.registered
class AetherhubWriteupDeckScraper(AetherhubDeckScraper):
    """Scraper of Aetherhub deck write-up page.
    """
    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return "aetherhub.com/decks/writeups/" in url.lower()

    @staticmethod
    @override
    def sanitize_url(url: str) -> str:
        return strip_url_query(url)

    @override
    def _get_sub_parser(self) -> AetherhubDeckTagParser:
        deck_tag = self._soup.find("div", id="tab_deck")
        if not deck_tag:
            raise ScrapingError("Deck tag not found", scraper=type(self), url=self.url)
        return AetherhubDeckTagParser(deck_tag, self._metadata)


@DeckUrlsContainerScraper.registered
class AetherhubUserScraper(DeckUrlsContainerScraper):
    """Scraper of Aetherhub user page.
    """
    SELENIUM_PARAMS = {  # override
        "xpath": '//table[@id="metaHubTable"]',
        "consent_xpath": CONSENT_XPATH,
        "wait_for_consent_disappearance": False
    }
    CONTAINER_NAME = "Aetherhub user"  # override
    DECK_SCRAPERS = AetherhubDeckScraper,  # override
    DECK_URL_PREFIX = URL_PREFIX  # override

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return "aetherhub.com/user/" in url.lower()

    @staticmethod
    @override
    def sanitize_url(url: str) -> str:
        url = strip_url_query(url)
        # links like: https://aetherhub.com/User/LegenVD/Decks/Standard-BO1 are already OK
        # links like: https://aetherhub.com/User/LegenVD/ need sanitization
        if "/decks/" not in url.lower() and not url.lower().endswith("/decks"):
            return f"{url}/decks"
        return url

    @override
    def _collect(self) -> list[str]:
        tbody = self._soup.find("tbody")
        if not tbody:
            raise ScrapingError("<tbody> tag not found", scraper=type(self), url=self.url)
        return [row.find("a")["href"] for row in tbody.find_all("tr") if row.find("a")]


@DeckUrlsContainerScraper.registered
class AetherhubEventScraper(DeckUrlsContainerScraper):
    """Scraper of Aetherhub event page.
    """
    SELENIUM_PARAMS = {  # override
        "xpath": '//tr[@class="deckdata"]',
        "consent_xpath": CONSENT_XPATH,
        "wait_for_consent_disappearance": False
    }
    CONTAINER_NAME = "Aetherhub event"  # override
    DECK_SCRAPERS = AetherhubDeckScraper,  # override
    DECK_URL_PREFIX = URL_PREFIX  # override

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return "aetherhub.com/events/" in url.lower()

    @staticmethod
    @override
    def sanitize_url(url: str) -> str:
        return strip_url_query(url)

    @override
    def _collect(self) -> list[str]:
        rows = self._soup.find_all("tr", class_="deckdata")
        if not rows:
            raise ScrapingError("Row tags not found", scraper=type(self), url=self.url)
        deck_tags = []
        for row in rows:
            _, deck_tag, *_ = row.find_all("td")
            deck_tags.append(deck_tag.find("a", href=lambda h: h and "/deck/" in h.lower()))
        deck_tags = [d for d in deck_tags if d is not None]
        return [deck_tag["href"] for deck_tag in deck_tags]


@HybridContainerScraper.registered
class AetherhubArticleScraper(HybridContainerScraper):
    """Scraper of Aetherhub article page.
    """
    CONTAINER_NAME = "Aetherhub article"  # override
    CONTAINER_SCRAPERS = AetherhubUserScraper, AetherhubEventScraper  # override
    SELENIUM_PARAMS = {  # override
        "xpath": '//div[@id="article-text"]',
        "consent_xpath": CONSENT_XPATH,
        "wait_for_consent_disappearance": False
    }

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return "aetherhub.com/article/" in url.lower()

    @staticmethod
    @override
    def sanitize_url(url: str) -> str:
        return strip_url_query(url)

    @override
    def _collect(self) -> tuple[list[str], list[Tag], list[Json], list[str]]:
        article_tag = self._soup.find("div", id="article-text")
        if article_tag is None:
            raise ScrapingError("Article tag not found", scraper=type(self), url=self.url)
        deck_links, container_links = self._find_links_in_tags(article_tag, url_prefix=URL_PREFIX)
        return deck_links, [], [], container_links
