"""

    mtg.deck.scrapers.playingmtg
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape PlayingMTG decklists.

    @author: mazz3rr

"""
import logging
from typing import override

import dateutil.parser
from bs4 import Tag

from mtg.deck.scrapers.abc import DeckScraper, DeckUrlsContainerScraper, HybridContainerScraper
from mtg.lib.numbers import extract_float, extract_int
from mtg.lib.scrape.core import (
    ScrapingError, find_links, find_next_sibling_tag,
    is_more_than_root_path, normalize_url, prepend_url, strip_url_query,
)
from mtg.scryfall import Card

_log = logging.getLogger(__name__)
URL_PREFIX = "https://playingmtg.com"


@DeckScraper.registered
class PlayingMtgDeckScraper(DeckScraper):
    """Scraper of PlayingMTG decklist page.
    """
    SELENIUM_PARAMS = {  # override
        "xpath": '//article//div/a[contains(@href, "/playingmtg.com/cards/")]'
    }
    EXAMPLE_URLS = (
        "https://playingmtg.com/decks/dimir-midrange-t0wk2",
        "https://playingmtg.com/decks/thurid-mare-of-destiny-equine-copy-00w7t/",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return is_more_than_root_path(url, "playingmtg.com", "decks")

    @classmethod
    @override
    def normalize_url(cls, url: str) -> str:
        url = normalize_url(url, case_sensitive=True)
        return strip_url_query(url)

    @override
    def _parse_input_for_metadata(self) -> None:
        if title_tag := self._soup.select_one("h1.page-title"):
            self._metadata["name"] = title_tag.attrs["title"]
        if fmt_snap := self._soup.find("span", string=lambda s: s and "Format:" in s):
            fmt_tag = fmt_snap.parent
            self._update_fmt(fmt_tag.find("a").text.strip().removeprefix("Format: "))
        info_tags = [*self._soup.select_one("ul.entry-meta").find_all("li")]
        if len(info_tags) == 2:
            author_tag, date_tag = info_tags
            theme_tag = None
        elif len(info_tags) == 3:
            theme_tag, author_tag, date_tag = info_tags
        else:
            theme_tag, author_tag, date_tag = None, None, None
        if theme_tag:
            self._update_archetype_or_theme(theme_tag.text.strip())
        if author_tag:
            self._metadata["author"] = author_tag.text.strip()
        if date_tag:
            self._metadata["date"] = dateutil.parser.parse(date_tag.text.strip()).date()
        if desc_h := self._soup.find("h2", string=lambda s: s and s == "Deck Description"):
            desc_tag = desc_h.parent
            self._metadata["description"] = desc_tag.text.strip().removeprefix("Deck Description")

    @classmethod
    def _parse_card_tag(cls, card_tag: Tag) -> list[Card]:
        data_tags = [
            tag for tag in card_tag.find_all("div")
            if tag.has_attr("title") and all(t not in tag.text for t in ("$", "N/A"))]
        qty, name = None, None
        for tag in data_tags:
            if all(ch.isdigit() for ch in tag.text):
                qty = int(tag.text)
            else:
                name = tag.text.strip()
        return cls.get_playset(cls.find_card(name), qty)

    @override
    def _parse_input_for_decklist(self) -> None:
        maindeck_hook = self._soup.find("div", string=lambda s: s and s == "Main Board")
        if not maindeck_hook:
            raise ScrapingError("Main board <div> tag not found")
        maindeck_tag = maindeck_hook.parent
        card_tags = [a_tag.parent for a_tag in maindeck_tag.find_all(
            "a", href=lambda h: h and "playingmtg.com/" in h)]
        for card_tag in card_tags:
            self._maindeck += self._parse_card_tag(card_tag)

        if sideboard_hook := self._soup.find("div", string=lambda s: s and s == "Side Board"):
            sideboard_tag = sideboard_hook.parent
            card_tags = [a_tag.parent for a_tag in sideboard_tag.find_all(
                "a", href=lambda h: h and "playingmtg.com/" in h)]
            for card_tag in card_tags:
                self._sideboard += self._parse_card_tag(card_tag)

        if commander_hook := self._soup.find("div", string=lambda s: s and s == "Commander"):
            commander_tag = commander_hook.parent
            card_tags = [a_tag.parent for a_tag in commander_tag.find_all(
                "a", href=lambda h: h and "playingmtg.com/" in h)]
            for card_tag in card_tags:
                self._set_commander(self._parse_card_tag(card_tag)[0])


@DeckUrlsContainerScraper.registered
class PlayingMtgTournamentScraper(DeckUrlsContainerScraper):
    """Scraper of PlayingMTG tournament page.
    """
    SELENIUM_PARAMS = {  # override
        "xpath": '//div[text()="Event Date"]',
        "wait_for_all": True
    }
    THROTTLING = DeckUrlsContainerScraper.THROTTLING * 2  # override
    CONTAINER_NAME = "PlayingMTG tournament"  # override
    DECK_SCRAPER_TYPES = PlayingMtgDeckScraper,  # override
    DECK_URL_PREFIX = URL_PREFIX  # override
    EXAMPLE_URLS = (
        "https://playingmtg.com/tournaments/mtgo-league-3164/",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return is_more_than_root_path(url, "playingmtg.com", "tournaments")

    def _parse_input_for_metadata(self) -> None:
        if event_date_hook := self._soup.find("div", string=lambda s: s and s == "Event Date"):
            self._metadata["event"] = {}
            # date
            date_tag = event_date_hook.parent
            date_text = date_tag.text.strip().removeprefix("Event Date").strip()
            self._metadata["event"]["date"] = dateutil.parser.parse(date_text).date()
            # name
            name_tag = find_next_sibling_tag(date_tag)
            if not name_tag:
                return
            self._metadata["event"]["name"] = name_tag.text.strip()
            # format and latest set
            fmt_set_tag = find_next_sibling_tag(name_tag)
            if not fmt_set_tag:
                return
            for tag in fmt_set_tag.find_all("div"):
                if "Latest set: " in tag.text:
                    self._metadata["event"]["latest_set"] = tag.text.strip().strip().removeprefix(
                        "Latest set: ").strip().lower()
                else:
                    self._metadata["event"]["format"] = tag.text.strip().lower()
            # themes
            info_tag = find_next_sibling_tag(fmt_set_tag)
            if not info_tag:
                return
            theme_tags = info_tag.find_all("div")
            self._metadata["event"]["themes"] = []
            for tag in theme_tags:
                data = {}
                if theme_name_tag := tag.find("small"):
                    data["name"] = theme_name_tag.text.strip()
                if theme_share_tag := tag.find("div"):
                    data["share"] = extract_float(theme_share_tag.text.strip())
                if data:
                    self._metadata["event"]["themes"].append(data)
            # players and winner
            players_tag = find_next_sibling_tag(info_tag)
            if not players_tag:
                return
            self._metadata["event"]["players"] = extract_int(players_tag.text.strip())
            winner_tag = find_next_sibling_tag(players_tag)
            if not winner_tag:
                return
            self._metadata["event"]["winner"] = winner_tag.text.strip().removeprefix(
                "Winner").strip()

    @override
    def _parse_input_for_decks_data(self) -> None:
        self._deck_urls = find_links(
            self._soup, href=lambda h: h and "/decks/" in h and "playingmtg.com/" not in h)


# TODO: tags-based decklists scraping (only if PlayingMTG articles start to be regularly featured
#  in YT videos)
@HybridContainerScraper.registered
class PlayingMtgArticleScraper(HybridContainerScraper):
    """Scraper of PlayingMTG article page.
    """
    SELENIUM_PARAMS = {  # override
        "xpath": '//div[@class="RootOfEmbeddedDeck"]//a[contains(@href, "/decks/")]',
        "wait_for_all": True
    }
    THROTTLING = DeckUrlsContainerScraper.THROTTLING * 2  # override
    CONTAINER_NAME = "PlayingMTG article"  # override
    CONTAINER_SCRAPER_TYPES = PlayingMtgTournamentScraper,  # override
    CONTAINER_URL_PREFIX = URL_PREFIX  # override
    EXAMPLE_URLS = (
        "https://playingmtg.com/pro-tour-aetherdrift-top-8-standard-decklists/",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        tokens = (
            "decks", "tournaments", "wp-content", "news", "mtg-arena", "spoilers", "commander",
            "standard", "modern", "pioneer", "collection", "prices", "products", "schedule",
            "builder", "meta", "tier-list"
        )
        if any(f"playingmtg.com/{t}" in url.lower() for t in tokens):
            return False
        return is_more_than_root_path(url, "playingmtg.com")

    @override
    def _parse_input_for_decks_data(self) -> None:
        deck_tags = [*self._soup.find_all("div", class_="RootOfEmbeddedDeck")]
        a_tags = [t.find("a", href=lambda h: h and "/decks/" in h) for t in deck_tags]
        deck_urls = [prepend_url(t["href"], URL_PREFIX) for t in a_tags if t]

        article_tag = self._soup.find("article")
        if not article_tag:
            err = ScrapingError("Article tag not found", scraper=type(self), url=self.url)
            _log.warning(f"Scraping failed with: {err!r}")
            self._deck_urls = deck_urls
            return

        p_deck_urls, self._container_urls = self._find_links_in_tags(*article_tag.find_all("p"))
        self._deck_urls = deck_urls + [l for l in p_deck_urls if l not in deck_urls]
