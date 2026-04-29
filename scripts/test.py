"""

    scripts.test
    ~~~~~~~~~~~~
    Test validity of the scraping logic against live websites using known valid URLs.

    @author: mazz3rr

"""
import logging
import sys

from mtg.lib.time import timed
from mtg.logging import init_log
from mtg.deck.scrapers.abc import (
    DeckScraper, DeckUrlsContainerScraper, DecksJsonContainerScraper, DeckTagsContainerScraper,
    HybridContainerScraper)


_log = logging.getLogger(__name__)

# TODO: make this work async (group URLs into batches and run concurrently)


# TODO: finish curating the URLs
TEST_URLS = [
    # CURATED ABOVE / UNCURATED BELOW
    'https://magic.wizards.com/en/news/archive?author=1iuVapWGRdDkVKUHT2xffq',
    'https://magic.wizards.com/en/news/feature/upgrading-the-miracle-worker-duskmourn-house-of-horror-commander-deck',
    'https://magicjank.com/magicjank-explorer-best-decks/',
    'https://mtgcircle.com/creators/numbskull/articles',
    'https://mtgdecks.net/authors/skura',
    'https://www.mtggoldfish.com/articles/search?author=93',
    'https://mtgmeta.io/articles/author/vertyx/'
    'https://mtgrocks.com/author/zachary-fink/',
    'https://mtgrocks.com/songcrafter-mage-temur-turns-modern-mtg/',
    'https://www.mtgsalvation.com/articles/features/49796-in-defense-of-the-pre-constructed-magic-deck',
    'https://www.mtgsalvation.com/decks/16487-w-lifegain',
    'https://www.mtgstocks.com/decks/481330',
    'https://www.mtgstocks.com/news/1061-weekly-winners-2021---20',
    'https://www.mtgvault.com/ahendra/',
    'https://www.mtgvault.com/rogib/decks/edh-for-fun-special-garruk/',
    'https://articles.nerdragegaming.com/the-start-of-something-1st-place-at-nrgchamp/',
    'https://www.pauperwave.com/author/crila-peoty/',
    'https://www.pauperwave.com/top-8-master-of-pauper-vol-2',
    'https://pennydreadfulmagic.com/seasons/33/people/id/2999/',
    'https://playingmtg.com/author/dirkbondster/',
    'https://playingmtg.com/pro-tour-aetherdrift-top-8-standard-decklists/',
    'https://playingmtg.com/tournaments/mtgo-league-3164/',
    'https://www.quietspeculation.com/2023/07/faces-of-aggro-boros-pia-aggro-in-pioneer/',
    'https://spikesacademy.com/p/deck-spotlight-ub-mill',
    'https://old.starcitygames.com/content/bennie-smith-decks',
    'https://articles.starcitygames.com/author/john-hall/',
    'https://tappedout.net/mtg-decks/14-11-17-modern-bant',
    'https://tcgrocks.com/mtg/deck-builder/embed/627c8696-51db-4f09-8c28-5f263f8713e1',
    'https://infinite.tcgplayer.com/article/What-is-Dand%C3%A2n-MTG-s-Forgetful-Fish-Format/7d6590b5-8e78-44f5-92c6-511049676fea/',
    'https://infinite.tcgplayer.com/author/Critical-Role',
    'https://infinite.tcgplayer.com/magic-the-gathering/decks/advanced-search?author=SBMTGDev&p=1',
    'https://infinite.tcgplayer.com/magic-the-gathering/decks/player/SBMTGDev',
    'https://infinite.tcgplayer.com/magic-the-gathering/events/event/MTGO%20Standard%20Challenge%2032%20-%2011-12-2024',
    'https://www.thegamer.com/magic-the-gathering-mtg-braids-cabal-minion-commander-deck-guide/',
    'https://thegathering.gg/neat-decking-11-23/',
    'https://thegathering.gg/standard-decks/gruul-aggro/',
    'https://themanabase.com/author/spirit-squad-mtg/',
    'https://topdeck.gg/bracket/D1NrlZYtYPr8HZgygm2G',
    'https://topdeck.gg/deck/jeweled-lotus-lattenkamp-2025/VXJvfKI8RrPtKdYYC2zMBbuAPgu1',
    'https://topdeck.gg/profile/XThcd3jrjqTHleEtnr9FAm3kIIv1',
    'https://ultimateguard.com/en/blog/a-breakdown-of-standard-gruul-vs-dimir-midrange-magic-the-gathering-seth-manfield',
    # UNSUPPORTED
    'https://burnmana.com/en/mtg-decks/standard/mono-red-aggro/fced354d-9a02-4c2b-abc0-f74393f65301',
    'https://app.cardboard.live/s/anzidmtg',
    'https://blog.cardsphere.com/sphere-of-influence-july-11-2025/',
    'https://www.dicebreaker.com/games/magic-the-gathering-game/best-games/best-mtg-arena-decks',
    'https://magic.facetofacegames.com/f2f-tour-halifax-2025-modern-super-qualifier-top-8-decklists/',
    'https://www.fanfinity.gg/blog/5-modern-decks-supercharged-with-final-fantasy/'
    'https://www.hipstersofthecoast.com/2025/03/jundjund-a-dandan-variant-for-midrange-players/',
    'https://krakenthemeta.com/deck-view?deckId=S4s10xy9vDTErG4jJ8kY',
]


@timed("testing scrapers")
def test_scrapers():
    """Test all registered scrapers with their example URLs.
    """
    passed, failed = [], []

    scrapers: list[type[DeckScraper]] = []
    scrapers += DeckScraper.get_registered_scrapers()
    scrapers += DeckUrlsContainerScraper.get_registered_scrapers()
    scrapers += DecksJsonContainerScraper.get_registered_scrapers()
    scrapers += DeckTagsContainerScraper.get_registered_scrapers()
    scrapers += HybridContainerScraper.get_registered_scrapers()

    for i, scraper in enumerate(scrapers, start=1):
        name = scraper.__name__
        _log.info(f"Testing {i}/{len(scrapers)} scraper: {name!r}...")
        result, exc = scraper.test()
        if result:
            _log.info(f"✓ {name!r} scraper: PASSED")
            passed.append(scraper)
        else:
            _log.warning(f"✗ {name!r} scraper: FAILED - {exc or 'no decks scraped'}")
            failed.append(scraper)

        _log.info(f"{len(passed)} scrapers passed. {len(failed)} scrapers failed.")


if __name__ == '__main__':
    init_log()
    sys.exit(test_scrapers())
