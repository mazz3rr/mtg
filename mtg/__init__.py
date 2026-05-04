"""

    mtg
    ~~~
    Root package.

    @author: mazz3rr

"""
from mtg.logging import init_log

__version__ = "0.10"
__description__ = "Scrape data on MtG decks."
__author__ = "mazz3rr"
__license__ = "MIT License"


init_log()


# import scraper modules
# so all their scraper classes get registered in the superclasses' they derive from on time
from mtg.deck.scrapers.aetherhub import DeckScraper
from mtg.deck.scrapers.archidekt import DeckScraper
from mtg.deck.scrapers.cardboardlive import DeckScraper
from mtg.deck.scrapers.cardhoarder import DeckScraper
from mtg.deck.scrapers.cardsrealm import DeckScraper
from mtg.deck.scrapers.coolstuff import HybridContainerScraper
from mtg.deck.scrapers.cycles import HybridContainerScraper
from mtg.deck.scrapers.deckbox import DeckScraper
from mtg.deck.scrapers.deckstats import DeckScraper
from mtg.deck.scrapers.draftsim import DeckScraper
from mtg.deck.scrapers.edhrec import DeckScraper
from mtg.deck.scrapers.abc import DeckUrlsContainerScraper, HybridContainerScraper
from mtg.deck.scrapers.flexslot import DeckScraper
from mtg.deck.scrapers.goldfish import DeckScraper
from mtg.deck.scrapers.hareruya import DeckScraper
from mtg.deck.scrapers.herald import HybridContainerScraper
from mtg.deck.scrapers.magic import DeckScraper
from mtg.deck.scrapers.magicblogs import HybridContainerScraper
from mtg.deck.scrapers.magicville import DeckScraper
from mtg.deck.scrapers.manabox import DeckScraper
from mtg.deck.scrapers.manastack import DeckScraper
from mtg.deck.scrapers.manatraders import DeckScraper
from mtg.deck.scrapers.melee import DeckScraper
from mtg.deck.scrapers.moxfield import DeckScraper
from mtg.deck.scrapers.mtgazone import DeckScraper
from mtg.deck.scrapers.mtgcircle import DeckScraper
from mtg.deck.scrapers.mtgdecksnet import DeckScraper
from mtg.deck.scrapers.mtgjson import DeckScraper
from mtg.deck.scrapers.mtgmeta import DeckScraper
from mtg.deck.scrapers.mtgo import DeckScraper
from mtg.deck.scrapers.mtgstocks import DeckScraper
from mtg.deck.scrapers.mtgtop8 import DeckScraper
from mtg.deck.scrapers.mtgvault import DeckScraper
from mtg.deck.scrapers.paupermtg import DeckScraper
from mtg.deck.scrapers.pauperwave import HybridContainerScraper
from mtg.deck.scrapers.penny import DeckScraper
from mtg.deck.scrapers.playingmtg import DeckScraper
from mtg.deck.scrapers.searchit import DeckScraper
from mtg.deck.scrapers.seventeen import DeckScraper
from mtg.deck.scrapers.scryfall import DeckScraper
from mtg.deck.scrapers.scg import DeckScraper
from mtg.deck.scrapers.streamdecker import DeckScraper
from mtg.deck.scrapers.tappedout import DeckScraper
from mtg.deck.scrapers.tcdecks import DeckScraper
from mtg.deck.scrapers.tcgplayer import DeckScraper
from mtg.deck.scrapers.tcgrocks import DeckScraper
from mtg.deck.scrapers.topdeck import DeckScraper
from mtg.deck.scrapers.topdecked import DeckScraper
from mtg.deck.scrapers.untapped import DeckScraper
from mtg.deck.scrapers.wotc import HybridContainerScraper
