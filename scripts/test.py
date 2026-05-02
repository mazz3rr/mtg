"""

    scripts.test
    ~~~~~~~~~~~~
    Test validity of the scraping logic against live websites using known valid URLs.

    @author: mazz3rr

"""
import logging
import sys
from operator import attrgetter

from mtg.lib.time import timed
from mtg.logging import init_log
from mtg.deck.scrapers.abc import (
    DeckScraper, DeckUrlsContainerScraper, DecksJsonContainerScraper, DeckTagsContainerScraper,
    HybridContainerScraper)


_log = logging.getLogger(__name__)

# TODO: make this work async (group URLs into batches and run concurrently)

# UNSUPPORTED URLS
# 'https://app.cardboard.live/s/anzidmtg',
# 'https://articles.nerdragegaming.com/the-start-of-something-1st-place-at-nrgchamp/',
# 'https://articles.starcitygames.com/author/john-hall/',
# 'https://blog.cardsphere.com/sphere-of-influence-july-11-2025/',
# 'https://burnmana.com/en/mtg-decks/standard/mono-red-aggro/fced354d-9a02-4c2b-abc0-f74393f65301',
# 'https://krakenthemeta.com/deck-view?deckId=S4s10xy9vDTErG4jJ8kY',
# 'https://magic.facetofacegames.com/f2f-tour-halifax-2025-modern-super-qualifier-top-8-decklists/',
# 'https://magicjank.com/magicjank-explorer-best-decks/',
# 'https://mtgcircle.com/creators/numbskull/articles',
# 'https://mtgdecks.net/authors/skura',
# 'https://mtgmeta.io/articles/author/vertyx/'
# 'https://playingmtg.com/author/dirkbondster/',
# 'https://spikesacademy.com/p/deck-spotlight-ub-mill',
# 'https://thegathering.gg/neat-decking-11-23/',
# 'https://thegathering.gg/standard-decks/gruul-aggro/',
# 'https://themanabase.com/author/spirit-squad-mtg/',
# 'https://ultimateguard.com/en/blog/a-breakdown-of-standard-gruul-vs-dimir-midrange-magic-the-gathering-seth-manfield',
# 'https://www.dicebreaker.com/games/magic-the-gathering-game/best-games/best-mtg-arena-decks',
# 'https://www.fanfinity.gg/blog/5-modern-decks-supercharged-with-final-fantasy/'
# 'https://www.hipstersofthecoast.com/2025/03/jundjund-a-dandan-variant-for-midrange-players/',
# 'https://www.mtgsalvation.com/articles/features/49796-in-defense-of-the-pre-constructed-magic-deck',
# 'https://www.mtgsalvation.com/decks/16487-w-lifegain',
# 'https://www.pauperwave.com/author/crila-peoty/',
# 'https://www.quietspeculation.com/2023/07/faces-of-aggro-boros-pia-aggro-in-pioneer/',
# 'https://www.thegamer.com/magic-the-gathering-mtg-braids-cabal-minion-commander-deck-guide/',


@timed("testing scrapers")
def test_scrapers(*scraper_types: type[DeckScraper]) -> None:
    """Test all registered scrapers with their example URLs.
    """
    if not scraper_types:
        scraper_types: list[type[DeckScraper]] = []
        scraper_types += DeckScraper.get_registered_scrapers()
        scraper_types += DeckUrlsContainerScraper.get_registered_scrapers()
        scraper_types += DecksJsonContainerScraper.get_registered_scrapers()
        scraper_types += DeckTagsContainerScraper.get_registered_scrapers()
        scraper_types += HybridContainerScraper.get_registered_scrapers()

    passed, failed = [], []
    messages = []
    scraper_types = sorted(scraper_types, key=attrgetter("__name__"))
    for i, scraper_type in enumerate(scraper_types, start=1):
        name = scraper_type.__name__
        _log.info(f"Testing {i}/{len(scraper_types)} scraper: {name!r}...")
        url, is_success, exc = scraper_type.test()
        if is_success:
            msg = f"✓ {name!r} scraper: PASSED"
            _log.info(msg)
            passed.append(scraper_type)
        else:
            msg = f"✗ {name!r} scraper: FAILED on {url!r} - {repr(exc) or 'no decks scraped'}"
            _log.warning(msg)
            failed.append(scraper_type)
        messages.append(msg)

    total = len(scraper_types)
    _log.info(
        f"{len(passed)}/{total} ({len(passed)/total:.1%}) scrapers PASSED. {len(failed)}/{total} "
        f"({len(failed)/total:.1%}) scrapers FAILED")
    for msg in messages:
        _log.info(f"\t{msg}")


if __name__ == '__main__':
    init_log()
    sys.exit(test_scrapers())
