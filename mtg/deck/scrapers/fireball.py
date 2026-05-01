"""

    mtg.deck.scrapers.fireball
    ~~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape ChannelFireball decklists.

    ChannelFireball is a TCG Player subsidiary since 2022. As such, its webpages use TCG Player's
    backend.

    @author: mazz3rr

"""
import logging
from typing import override

from mtg.deck.scrapers.abc import (
    DeckScraper, DeckUrlsContainerScraper, DecksJsonContainerScraper,
    HybridContainerScraper,
)
from mtg.deck.scrapers.tcgplayer import (
    TcgPlayerInfiniteArticleScraper, TcgPlayerInfiniteAuthorScraper, TcgPlayerInfiniteDeckScraper,
    TcgPlayerInfinitePlayerScraper,
)
from mtg.yt.discover import UrlHook

# NOTE: As of Feb 25th, TCGPlayer seems to have recently withdrawn special ChannelFireball-specific
# API domain: 'cfg-infinite-api.tcgplayer.com' that the scrapers below used to utilize. Now there's
# a simple redirection from ChannelFireball URL directly to TCGPlayer Infinite article and the
# regular TCGPlayer Infinite API domain is used.


# TODO: #471


_log = logging.getLogger(__name__)
FIREBALL_URL_PREFIX = "https://www.channelfireball.com"
URL_HOOKS = (
    # deck
    UrlHook(
        ('"channelfireball.com/magic-the-gathering/deck/"', ),
    ),
    # player
    UrlHook(
        ('"channelfireball.com/magic-the-gathering/decks/player/"', ),
    ),
    # article
    UrlHook(
        ('"channelfireball.com/article/"', ),
    ),
    # author
    UrlHook(
        ('"channelfireball.com/author/"', ),
    ),
)


@DeckScraper.registered
class ChannelFireballDeckScraper(TcgPlayerInfiniteDeckScraper):
    """Scraper of ChannelFireball decklist page.
    """
    EXAMPLE_URLS = (
        "https://www.channelfireball.com/magic-the-gathering/deck/Timeless-Grixis/481595?external=undefined",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return "channelfireball.com/magic-the-gathering/deck/" in url.lower()


@DeckUrlsContainerScraper.registered
class ChannelFireballPlayerScraper(TcgPlayerInfinitePlayerScraper):
    """Scraper of ChannelFireball player page.
    """
    CONTAINER_NAME = "ChannelFireball player"  # override
    DECK_SCRAPER_TYPES = ChannelFireballDeckScraper,  # override
    DECK_URL_PREFIX = FIREBALL_URL_PREFIX  # override
    EXAMPLE_URLS = (
        "https://www.channelfireball.com/magic-the-gathering/decks/player/Martin%20Juza",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return "channelfireball.com/magic-the-gathering/decks/player/" in url.lower()


@DecksJsonContainerScraper.registered
class ChannelFireballArticleScraper(TcgPlayerInfiniteArticleScraper):
    """Scraper of ChannelFireball article page.
    """
    CONTAINER_NAME = "ChannelFireball article"  # override
    EXAMPLE_URLS = (
        "https://www.channelfireball.com/article/MTG-Deck-Guide-Standard-Gruul-Aggro/bd06ac65-bb14-442c-aed5-cb9195861496/",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return f"channelfireball.com/article/" in url.lower()


@HybridContainerScraper.registered
class ChannelFireballAuthorScraper(TcgPlayerInfiniteAuthorScraper):
    """Scraper of ChannelFireball author page.
    """
    CONTAINER_NAME = "ChannelFireball author"  # override
    CONTAINER_SCRAPER_TYPES = ChannelFireballArticleScraper,  # override
    CONTAINER_URL_PREFIX = FIREBALL_URL_PREFIX  # override
    EXAMPLE_URLS = (
        "https://www.channelfireball.com/author/Frank-Karsten/7f203152-211a-478d-8fee-464c2aeca2cd",
    )

    @classmethod
    @override
    def is_valid_url(cls, url: str) -> bool:
        return "channelfireball.com/author/" in url.lower() and not url.lower().endswith("/decks")
