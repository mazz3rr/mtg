"""

    mtg.deck.core
    ~~~~~~~~~~~~~
    Parse data into Deck objects.

    @author: mazz3rr

"""
import contextlib
import itertools
import logging
import re
from abc import ABC, abstractmethod
from collections import Counter
from enum import Enum, auto
from functools import cached_property
from operator import attrgetter, itemgetter
from typing import Any, Iterable, Iterator, Self

from mtg.constants import Json
from mtg.lib.check_type import type_checker
from mtg.lib.common import ParsingError, from_iterable, get_hash, getrepr, remove_furigana
from mtg.lib.json import to_json
from mtg.lib.scrape.core import get_netloc_domain
from mtg.scryfall import COMMANDER_FORMATS, Card, Color, \
    MULTIFACE_SEPARATOR as SCRYFALL_MULTIFACE_SEPARATOR, aggregate, all_formats, \
    find_by_cardmarket_id, find_by_collector_number, find_by_mtgo_id, find_by_name, \
    find_by_oracle_id, find_by_scryfall_id, find_by_tcgplayer_id, find_sets, query_api_for_card

_log = logging.getLogger(__name__)

ARENA_MULTIFACE_SEPARATOR = " /// "  # this is different from Scryfall data where they use: ' // '

# TODO: look here: https://pennydreadfulmagic.com/archetypes/
# based on https://draftsim.com/mtg-archetypes/
# this listing omits combo-control as it's too long a name to be efficiently used as a component
# of a catchy deck name
# in those scenarios usually a deck's theme (sub-archetype) is used (e.g. "stax" or "prison")
class Archetype(Enum):
    AGGRO = "aggro"
    MIDRANGE = "midrange"
    CONTROL = "control"
    COMBO = "combo"
    TEMPO = "tempo"
    RAMP = "ramp"

# this is needed when scraping meta-decks from sites that subdivide meta based on the mode of
# play (e.g. Aetherhub)
class Mode(Enum):
    BO1 = "Bo1"
    BO3 = "Bo3"

# themes compiled from:
# https://edhrec.com/themes
# https://edhrec.com/typal
# https://draftsim.com/mtg-deck-themes/
# https://cardgamebase.com/commander-precons/
# https://www.mtgsalvation.com/forums/the-game/commander-edh/806251-all-the-commander-edh-deck-archetypes-and-themes
# https://www.mtggoldfish.com/metagame/
# https://mtgdecks.net/Modern/staples/
# TODO: use Python's 'inflection' lib to handle key variants (#418)
THEMES = {
    "Affinity",  # mechanic
    "Aggression",
    "Allies",  # tribal
    "Angels",  # tribal
    "Apes",  # tribal
    "Apostles",  # (Shadowborn Apostles)
    "Approach",  # (Dragon's Approach)
    "Arcane",
    "Archers",  # tribal
    "Aristocrats",
    "Artifact",
    "Artifacts",
    "Artificers",  # tribal
    "Assassins",  # tribal
    "Atogs",  # tribal
    "Auras",
    "Avatars",  # tribal
    "Backup",  # mechanic
    "Barbarians",  # tribal
    "Bears",  # tribal
    "Beasts",  # tribal
    "Berserkers",  # tribal
    "Big-Mana",
    "Birds",  # tribal
    "Blink",
    "Blitz",  # mechanic
    "Blood",
    "Bogles",
    "Bounce",
    "Bully",
    "Burn",
    "Cantrips",
    "Card-Draw",
    "Cascade",  # mechanic
    "Casualty",  # mechanic
    "Cats",  # tribal
    "Cephalids",  # tribal
    "Chaos",
    "Cheerios",
    "Clerics",  # tribal
    "Clones",
    "Clues",
    "Connive",  # mechanic
    "Constructs",  # tribal
    "Convoke",  # mechanic
    "Counters",
    "Counterspells",
    "Coven",  # mechanic
    "Crabs",  # tribal
    "Curses",
    "Cycling",  # mechanic
    "Deathtouch",  # mechanic
    "Defenders",  # mechanic
    "Deflection",
    "Demons",  # tribal
    "Deserts",
    "Detectives",  # tribal
    "Devils",  # tribal
    "Devotion",  # mechanic
    "Dinosaurs",  # tribal
    "Discard",
    "Doctors",  # tribal
    "Dogs",  # tribal
    "Domain",  # mechanic
    "Dragons",  # tribal
    "Drakes",  # tribal
    "Draw-Go",
    "Dredge",
    "Druids",  # tribal
    "Dungeons",  # mechanic
    "Dwarves",  # tribal
    "Eggs",
    "Elders",  # tribal
    "Eldrazi",  # tribal
    "Elementals",  # tribal
    "Elephants",  # tribal
    "Elves",  # tribal
    "Enchantments",
    "Enchantress",
    "Energy",  # mechanic
    "Enrage",  # mechanic
    "Equipment",
    "Equipments",
    "Evasion",
    "Exile",
    "Explore",  # mechanic
    "Extra-Combat",
    "Extra-Combats",
    "Extra-Turns",
    "Face-Down",
    "Faeries",  # tribal
    "Fight",
    "Flash",  # mechanic
    "Flashback",  # mechanic
    "Flicker",
    "Fliers",
    "Flying",  # mechanic
    "Food",
    "Forced-Combat",
    "Foretell",  # mechanic
    "Foxes",  # tribal
    "Frogs",  # tribal
    "Fungi",  # tribal
    "Giants",  # tribal
    "Go-Wide",
    "Goad",  # mechanic
    "Goblins",  # tribal
    "Gods",  # tribal
    "Golems",  # tribal
    "Gorgons",  # tribal
    "Graveyard",
    "Griffins",  # tribal
    "Halflings",  # tribal
    "Hate-Bears",
    "Hatebears",
    "Heroic",
    "Historic",  # mechanic
    "Horrors",  # tribal
    "Horses",  # tribal
    "Hug",  # (Group Hug)
    "Humans",  # tribal
    "Hydras",  # tribal
    "Illusions",  # tribal
    "Incubate",  # mechanic
    "Indestructible"  # mechanic
    "Infect",  # mechanic
    "Insects",  # tribal
    "Instants",
    "Jegantha",  # (Jegantha Companion)
    "Judo",
    "Kaheera",  # (Kaheera Companion)
    "Kavu",  # tribal
    "Keruga",  # (Keruga Companion)
    "Keywords",
    "Kithkin",  # tribal
    "Knights",  # tribal
    "Krakens",  # tribal
    "Land",
    "Land-Destruction",
    "Landfall",  # mechanic
    "Lands",
    "Lands",
    "Legends",
    "Life-Drain",
    "Life-Gain",
    "Life-Loss",
    "Lifedrain",
    "Lifegain",
    "Lifeloss",
    "Lords",  # tribal
    "Madness",  # mechanic
    "Mana-Rock",
    "Merfolk",  # tribal
    "Mill",  # mechanic
    "Minotaurs",  # tribal
    "Miracle",
    "Modify",  # mechanic
    "Monarch",  # mechanic
    "Monks",  # tribal
    "Moonfolk",  # tribal
    "Morph",  # mechanic
    "Mutants",  # tribal
    "Mutate",  # mechanic
    "Myr",  # tribal
    "Necrons",  # tribal
    "Ninjas",  # tribal
    "Ninjutsu",  # mechanic
    "One-Shot",
    "Oozes",  # tribal
    "Orcs",  # tribal
    "Outlaws",  # tribal
    "Overrun",
    "Party",  # mechanic
    "Permission",
    "Petitioners",  # (Persistent Petitioners)
    "Phoenixes",  # tribal
    "Phyrexians",  # tribal
    "Pillow-Fort",
    "Pingers",
    "Pirates",  # tribal
    "Planeswalkers",
    "Plants",  # tribal
    "Pod",
    "Poison",
    "Politics",
    "Polymorph",
    "Ponza",
    "Populate",
    "Power",
    "Praetors",  # tribal
    "Prison",
    "Prowess",  # mechanic
    "Rat-Colony",
    "Rats",  # tribal
    "Reanimator",
    "Rebels",  # tribal
    "Removal",
    "Rituals",
    "Robots",  # tribal
    "Rock",
    "Rogues",  # tribal
    "Sacrifice",
    "Sagas",
    "Samurai",  # tribal
    "Saprolings",  # (Saproling Tokens), tribal
    "Satyrs",  # tribal
    "Scam",
    "Scarecrows",  # tribal
    "Scry",
    "Sea-Creatures",
    "Self-Mill",
    "Shamans",  # tribal
    "Shapeshifters",  # tribal
    "Skeletons",  # tribal
    "Slivers",  # tribal
    "Slug",  # (Group Slug)
    "Snakes",  # tribal
    "Sneak-and-Tell",
    "Snow",
    "Soldiers",  # tribal
    "Sorceries",
    "Specters",  # tribal
    "Spell-Copy",
    "Spellslinger",
    "Sphinxes",  # tribal
    "Spiders",  # tribal
    "Spirits",  # tribal
    "Stax",
    "Stompy",
    "Storm",
    "Suicide",
    "Sunforger",
    "Superfriends",
    "Surge",  # (Primal Surge)
    "Surveil",  # mechanic
    "Suspend",
    "Swarm",
    "Taxes",
    "The-Rock",
    "Theft",
    "Thopters",  # tribal
    "Tokens",
    "Toolbox",
    "Top-Deck",
    "Topdeck",
    "Toughness",
    "Toxic",
    "Treasure",
    "Treasures",
    "Treefolk",  # tribal
    "Tribal",
    "Tron",
    "Turtles",  # tribal
    "Tutor",
    "Tutors",
    "Typal",
    "Tyranids",  # tribal
    "Umori",
    "Unicorns",  # tribal
    "Unnatural",
    "Value",
    "Vampires",  # tribal
    "Vehicles",
    "Venture",
    "Voltron",
    "Voting",
    "Walls",  # tribal
    "Warriors",  # tribal
    "Weenie",
    "Weird",
    "Werewolves",  # tribal
    "Wheels",
    "Wizards",  # tribal
    "Wolves",  # tribal
    "Wraiths",  # tribal
    "Wurms",  # tribal
    "X",
    "X-Creatures",
    "X-Spells",
    "Zombies",  # tribal
    "Zoo",
}


class InvalidDeck(ParsingError):
    """Raised on invalid deck.
    """


def sanitize_deck_source(src: str) -> str:
    src = src.lower().removeprefix("www.")
    if "cardsrealm.com" in src:
        return "mtg.cardsrealm.com"
    if "edhrec.com" in src:
        return "edhrec.com"
    if "hareruyamtg.com" in src:
        return "hareruyamtg.com"
    if "mtgmelee.com" in src:
        return "melee.gg"
    if "mtga.cc" in src:
        return "mtgarena.pro"
    if "starcitygames.com" in src:
        return "starcitygames.com"
    if "tcgplayer.com" in src:
        return "tcgplayer.com"
    return src


# this class tries to be as generic as possible and still support multiple Constructed formats
# this means some more complicated formats like Oathbreaker are not fully supported (e.g a Deck
# knows nothing about signature spells) to not over-complicate things (by either going into an
# inheritance hierarchy or bloating the generic API beyond comprehension)
class Deck:
    """A deck of Magic: the Gathering cards suitable for Constructed formats.
    """
    MIN_MAINDECK_SIZE = 60
    MAX_SIDEBOARD_SIZE = 15
    MIN_AGGRO_CMC = 2.3  # arbitrary
    MAX_CONTROL_CREATURES_COUNT = 10  # arbitrary

    @property
    def maindeck(self) -> list[Card]:
        return self._maindeck

    @property
    def sideboard(self) -> list[Card]:
        return self._sideboard

    @property
    def has_sideboard(self) -> bool:
        return bool(self.sideboard)

    @property
    def commander(self) -> Card | None:
        return self._commander

    @property
    def partner_commander(self) -> Card | None:
        return self._partner_commander

    @property
    def companion(self) -> Card | None:
        return self._companion

    @property
    def cards(self) -> list[Card]:
        commanders = [self.commander] if self.commander else []
        if self.partner_commander:
            commanders.append(self.partner_commander)
        return [*commanders, *self.maindeck, *self.sideboard]

    @property
    def color(self) -> Color:
        return Color.from_cards(*self.cards)

    @property
    def color_identity(self) -> Color:
        return Color.from_cards(*self.cards, identity=True)

    @property
    def artifacts(self) -> list[Card]:
        return [card for card in self.cards if card.is_artifact]

    @property
    def battles(self) -> list[Card]:
        return [card for card in self.cards if card.is_battle]

    @property
    def creatures(self) -> list[Card]:
        return [card for card in self.cards if card.is_creature]

    @property
    def enchantments(self) -> list[Card]:
        return [card for card in self.cards if card.is_enchantment]

    @property
    def instants(self) -> list[Card]:
        return [card for card in self.cards if card.is_instant]

    @property
    def lands(self) -> list[Card]:
        return [card for card in self.cards if card.is_land]

    @property
    def planeswalkers(self) -> list[Card]:
        return [card for card in self.cards if card.is_planeswalker]

    @property
    def sorceries(self) -> list[Card]:
        return [card for card in self.cards if card.is_sorcery]

    @property
    def commons(self) -> list[Card]:
        return [card for card in self.cards if card.is_common]

    @property
    def uncommons(self) -> list[Card]:
        return [card for card in self.cards if card.is_uncommon]

    @property
    def rares(self) -> list[Card]:
        return [card for card in self.cards if card.is_rare]

    @property
    def mythics(self) -> list[Card]:
        return [card for card in self.cards if card.is_mythic]

    @property
    def total_rarity_weight(self) -> float:
        return sum(card.rarity.weight for card in self.cards)

    @property
    def avg_rarity_weight(self):
        return self.total_rarity_weight / len(self.cards)

    @property
    def avg_cmc(self) -> float:
        manas = [card.cmc for card in self.cards if card.cmc]
        if not manas:
            return 0
        return sum(manas) / len(manas)

    @property
    def total_price(self) -> float:
        return sum(c.price for c in self.cards if c.price)

    @property
    def avg_price(self) -> float | None:
        cards = [card for card in self.cards if card.price]
        return self.total_price / len(cards) if cards else None

    @property
    def total_price_tix(self) -> float:
        return sum(c.price_tix for c in self.cards if c.price_tix)

    @property
    def avg_price_tix(self) -> float | None:
        cards = [card for card in self.cards if card.price_tix]
        return self.total_price_tix / len(cards) if cards else None

    @property
    def sets(self) -> list[str]:
        return sorted({c.set for c in self.cards if not c.is_basic_land})

    @property
    def races(self) -> Counter:
        return Counter(itertools.chain(*[c.races for c in self.cards]))

    @property
    def classes(self) -> Counter:
        return Counter(itertools.chain(*[c.classes for c in self.cards]))

    @property
    def is_bo3(self) -> bool:
        return self.has_sideboard and len(self.sideboard) > 7

    @property
    def is_bo1(self) -> bool:
        return not self.is_bo3

    @cached_property
    def theme(self) -> str | None:
        if theme := self.metadata.get("theme"):
            return theme

        if not self.name:
            return None
        nameparts = [
            p for p in self.name.split() if p.lower() not in [c.name.lower() for c in Color]]
        return from_iterable(
            THEMES, lambda th: any(p.title() == th.title() for p in nameparts))

    @cached_property
    def archetype(self) -> Archetype:
        if arch := self.metadata.get("archetype"):
            with contextlib.suppress(ValueError):
                return Archetype(arch)

        if self.name:
            nameparts = [
                p for p in self.name.split() if not p.title()
                in [c.name.title() for c in Color]]
            arch = from_iterable(
                Archetype, lambda a: any(p.title() == a.name.title() for p in nameparts))
            if arch:
                return arch
            # combo
            card_parts = {p for card in self.cards for p in card.name_parts}
            if any(p.title() in THEMES for p in nameparts):  # a themed deck is not a combo deck
                pass
            elif identified := from_iterable(nameparts, lambda n: n.lower() in card_parts):
                if self.commander and identified in self.commander.name_parts:
                    pass  # don't flag commander part in name as combo
                else:
                    return Archetype.COMBO
        if self.avg_cmc < self.MIN_AGGRO_CMC:
            return Archetype.AGGRO
        else:
            if len(self.creatures) < self.MAX_CONTROL_CREATURES_COUNT:
                return Archetype.CONTROL
            return Archetype.MIDRANGE

    @property
    def metadata(self) -> Json:
        return self._metadata

    @property
    def name(self) -> str | None:
        return self.metadata.get("name")

    @property
    def url(self) -> str | None:
        return self.metadata.get("url")

    @property
    def source(self) -> str:
        return self.url_to_source(self.url)

    @property
    def format(self) -> str | None:
        return self.metadata.get("format")

    @property
    def is_meta_deck(self) -> bool:
        return any(k.startswith("meta") for k in self.metadata)

    @property
    def is_event_deck(self) -> bool:
        return any(k.startswith("event") for k in self.metadata)

    @cached_property
    def latest_set(self) -> str | None:
        set_codes = {c.set for c in self.cards if not c.is_basic_land}
        sets = find_sets(lambda s: s.code in set_codes and s.is_expansion)
        if not sets:
            return None
        sets = sorted(sets, key=attrgetter("released_at"))
        return [s.code for s in sets][-1]

    @classmethod
    def url_to_source(cls, url: str | None) -> str:
        if not url:
            return "arena.decklist"
        return sanitize_deck_source(get_netloc_domain(url))

    def __init__(
            self, maindeck: Iterable[Card], sideboard: Iterable[Card] | None = None,
            commander: Card | None = None, partner_commander: Card | None = None,
            companion: Card | None = None, metadata: Json | None = None) -> None:
        commanders = [c for c in [commander, partner_commander] if c]
        maindeck, sideboard = [*maindeck], [*sideboard] if sideboard else []
        if commanders and sideboard:
            sideboard = []
            _log.warning("Disregarding sideboard for a commander-enabled deck")
        if partner_commander:
            if not commander:
                raise InvalidDeck("Partner commander without commander")
        if commanders:
            for cmd in commanders:
                if cmd in maindeck:
                    maindeck.remove(cmd)
                if cmd in sideboard:
                    sideboard.remove(cmd)
            cards = {*maindeck, *sideboard}
            if any(cmd in cards for cmd in commanders):
                raise InvalidDeck(f"Redundant commander inclusion")
            identity = {clr for c in commanders for clr in c.color_identity.value}
            for card in [*maindeck, *sideboard]:
                if any(letter not in identity for letter in card.color_identity.value):
                    _log.warning(
                        f"Color identity of '{card}' ({card.color_identity}) doesn't match "
                        f"commander's color identity ({Color.from_letters(*identity)})")
        self._commander, self._partner_commander = commander, partner_commander

        if companion:
            if not companion.is_companion:
                raise InvalidDeck(f"Not a companion card: '{companion}'")
        self._companion = companion

        sideboard = [*sideboard] if sideboard else []
        sideboard = [
            companion, *sideboard] if companion and companion not in sideboard else sideboard
        self._metadata = metadata or {}

        self._max_playset_count = 1 if commander is not None else 4
        playsets = aggregate(*maindeck)
        for playset in playsets.values():
            self._validate_playset(playset)
        self._maindeck = [*itertools.chain(
            *sorted(playsets.values(), key=lambda l: l[0].name))]

        if (len(self.maindeck) + len(commanders)) < self.MIN_MAINDECK_SIZE:
            raise InvalidDeck(
                f"Invalid deck size: {len(self.maindeck) + len(commanders)} "
                f"< {self.MIN_MAINDECK_SIZE}")

        self._sideboard = []
        if sideboard:
            if not self.companion:
                if comp := from_iterable(sideboard, lambda c: c.is_companion):
                    self._companion = comp
            sideboard_playsets = aggregate(*sideboard)
            self._sideboard = [*itertools.chain(
                *sorted(sideboard_playsets.values(), key=lambda l: l[0].name))]
            if len(self.sideboard) > self.MAX_SIDEBOARD_SIZE:
                self._cut_sideboard(sideboard)
            temp_playsets = aggregate(*self.cards)
            for playset in temp_playsets.values():
                self._validate_playset(playset)

    def _cut_sideboard(self, input_sideboard: list[Card]) -> None:
        _log.warning(
            f"Oversized sideboard ({len(self.sideboard)}) cut down to regular size "
            f"({self.MAX_SIDEBOARD_SIZE})")
        sideboard = input_sideboard[:self.MAX_SIDEBOARD_SIZE]
        if self.companion and self.companion not in sideboard:
            sideboard[-1] = self.companion
        sideboard_playsets = aggregate(*sideboard)
        self._sideboard = [*itertools.chain(
            *sorted(sideboard_playsets.values(), key=lambda l: l[0].name))]

    def _validate_playset(self, playset: list[Card]) -> None:
        card = playset[0]
        if card.is_basic_land or card.allowed_multiples is Ellipsis:
            pass
        else:
            max_playset = self._max_playset_count if card.allowed_multiples is None \
                else card.allowed_multiples
            if len(playset) > max_playset:
                raise InvalidDeck(
                    f"Too many occurrences of {card.name!r}: "
                    f"{len(playset)} > {max_playset}")

    def __repr__(self) -> str:
        reprs = [("name", self.name)] if self.name else []
        if self.format:
            reprs += [("format", self.format)]
        reprs += [
            ("color", self.color.name),
            ("mode", f"{Mode.BO3.value if self.is_bo3 else Mode.BO1.value}"),
            ("avg_cmc", f"{self.avg_cmc:.2f}"),
            ("avg_rarity_weight", f"{self.avg_rarity_weight:.1f}")
        ]
        if self.avg_price:
            reprs += [("avg_price", f"${self.avg_price:.2f}")]
        reprs += [
            ("artifacts", len(self.artifacts)),
            ("battles", len(self.battles)),
            ("creatures", len(self.creatures)),
            ("enchantments", len(self.enchantments)),
            ("instants", len(self.instants)),
            ("lands", len(self.lands)),
            ("planeswalkers", len(self.planeswalkers)),
            ("sorceries", len(self.sorceries)),
        ]
        if self.commander:
            reprs.append(("commander", str(self.commander)))
        if self.companion:
            reprs.append(("companion", str(self.companion)))
        return getrepr(self.__class__, *reprs)

    def __eq__(self, other: Self) -> bool:
        if not isinstance(other, Deck):
            return False
        return self.json == other.json

    def __hash__(self) -> int:
        return hash(self.json)

    def __lt__(self, other: Self) -> bool:
        if not isinstance(other, Deck):
            return NotImplemented
        return self.avg_cmc < other.avg_cmc

    def __iter__(self) -> Iterator[Card]:
        return iter(self.cards)

    def update_metadata(self, **data: Any) -> None:
        self._metadata.update(data)

    @staticmethod
    def _to_playset_line(playset: list[Card], with_printings=False) -> str:
        card = playset[0]
        card_name = card.name.replace(
            SCRYFALL_MULTIFACE_SEPARATOR,
            ARENA_MULTIFACE_SEPARATOR) if card.is_multifaced else card.name
        line = f"{len(playset)} {card_name}"
        if with_printings:
            line += f" ({card.set.upper()}) {card.collector_number}"
        return line

    def _build_decklist(self, with_printings=True, about=True) -> str:
        lines = []
        if about and self.metadata.get("name"):
            lines += ["About", f'Name {self.metadata["name"]}', ""]
        if self.commander:
            playset = aggregate(self.commander)[self.commander]
            lines += ["Commander", self._to_playset_line(playset, with_printings=with_printings)]
            if self.partner_commander:
                playset = aggregate(self.partner_commander)[self.partner_commander]
                lines += [self._to_playset_line(playset, with_printings=with_printings)]
            lines += [""]
        if self.companion:
            playset = aggregate(self.companion)[self.companion]
            lines += ["Companion", self._to_playset_line(playset, with_printings=with_printings), ""]
        deck_playsets = sorted(
            (playset for playset in aggregate(*self.maindeck).values()),
            key=lambda l: l[0].name)
        lines += [
            "Deck",
            *[self._to_playset_line(playset, with_printings=with_printings) for playset
              in deck_playsets]
        ]
        if self.sideboard:
            side_playsets = sorted(
                (playset for playset in aggregate(*self.sideboard).values()),
                key=lambda l: l[0].name)
            lines += [
                "",
                "Sideboard",
                *[self._to_playset_line(playset, with_printings=with_printings) for playset
                  in side_playsets]
            ]
        return "\n".join(lines)

    @cached_property
    def decklist(self) -> str:
        return self._build_decklist(with_printings=False, about=False)

    @property
    def decklist_hash(self) -> str:
        return get_hash(self.decklist, truncation=40, sep="-")

    @cached_property
    def decklist_with_printings(self) -> str:
        return self._build_decklist(with_printings=True, about=False)

    @property
    def decklist_with_printings_hash(self) -> str:
        return get_hash(self.decklist_with_printings, truncation=40, sep="-")

    @cached_property
    def json(self) -> str:
        """Return a JSON representation of this deck.
        """
        data = {
            "metadata": self.metadata,
            "decklist_hash": self.decklist_hash,
            "decklist_with_printings": self.decklist_with_printings_hash,
        }
        return to_json(data, sort_dictionaries=True)


class _ParsingStates(Enum):
    """Enumeration of parsing states.
    """
    IDLE = auto()
    MAINDECK = auto()
    SIDEBOARD = auto()
    COMMANDER = auto()
    COMPANION = auto()


class _ParsingState:
    """State machine for deck parsing.
    """
    @property
    def state(self) -> _ParsingStates:
        return self.__state

    @state.setter
    @type_checker(_ParsingStates, is_method=True)
    def state(self, value: _ParsingStates) -> None:
        if value is self.state:
            raise ParsingError(f"Invalid transition from {self.state.name!r} to {value.name!r}")
        self.__state = value

    @property
    def is_idle(self) -> bool:
        return self.state is _ParsingStates.IDLE

    @property
    def is_maindeck(self) -> bool:
        return self.state is _ParsingStates.MAINDECK

    @property
    def is_sideboard(self) -> bool:
        return self.state is _ParsingStates.SIDEBOARD

    @property
    def is_commander(self) -> bool:
        return self.state is _ParsingStates.COMMANDER

    @property
    def is_companion(self) -> bool:
        return self.state is _ParsingStates.COMPANION

    def __init__(self) -> None:
        self.__state = _ParsingStates.IDLE

    def shift_to_maindeck(self) -> None:
        self.state = _ParsingStates.MAINDECK

    def shift_to_sideboard(self) -> None:
        self.state = _ParsingStates.SIDEBOARD

    def shift_to_commander(self) -> None:
        self.state = _ParsingStates.COMMANDER

    def shift_to_companion(self) -> None:
        self.state = _ParsingStates.COMPANION

    def shift_to_idle(self) -> None:
        self.state = _ParsingStates.IDLE


class CardNotFound(ParsingError):
    """Raised on card not being found.
    """


# TODO: use Python's 'inflection' lib to handle key variants (#418)
SANITIZED_FORMATS = {
    "1v1 commander": "commander",
    "archon": "commander",
    "artisan historic": "historic",
    "artisanhistoric": "historic",
    "australian highlander": "commander",
    "australianhighlander": "commander",
    "canadian highlander": "commander",
    "canadianhighlander": "commander",
    "cedh": "commander",
    "centurion": "commander",
    "clegacy": "legacy",
    "cmodern": "modern",
    "commander / edh": "commander",
    "commander 1v1": "commander",
    "commander/edh": "commander",
    "commanderprecon": "commander",
    "commanderprecons": "commander",
    "cpauper": "pauper",
    "cpioneer": "pioneer",
    "cpdh": "paupercommander",
    "cstandard": "standard",
    "cvintage": "vintage",
    "duel commander": "duel",
    "duel-commander": "duel",
    "duelcommander": "duel",
    "duelcommanderrussian": "duel",
    "edh": "commander",
    "european highlander": "commander",
    "europeanhighlander": "commander",
    "future standard": "future",
    "highlander australian": "commander",
    "highlander canadian": "commander",
    "highlander european": "commander",
    "highlander": "commander",
    "highlanderaustralian": "commander",
    "highlandercanadian": "commander",
    "highlandereuropean": "commander",
    "historic brawl": "brawl",
    "historic pauper": "historic",
    "historic-pauper": "historic",
    "historicbrawl": "brawl",
    "historicpauper": "historic",
    "no banned list modern": "modern",
    "old school": "oldschool",
    "old-school": "oldschool",
    "oldschool 93/94": "oldschool",
    "past standard": "standard",
    "pauper commander": "paupercommander",
    "pauper edh": "paupercommander",
    "pauperedh": "paupercommander",
    "pedh": "paupercommander",
    "pdh": "paupercommander",
    "penny dreadful": "penny",
    "pre commander": "predh",
    "pre edh": "predh",
    "standard brawl": "standardbrawl",
    "vintage old school": "oldschool",
}
JAPANESE_FORMATS = {
    "アルケミー": "alchemy",
    "ブロール": "brawl",
    "統率者": "commander",
    "コマンダー": "commander",
    "デュエル": "duel",
    "エクスプローラー": "explorer",
    "フューチャースタンダード": "future",
    "グラディエーター": "gladiator",
    "ヒストリック": "historic",
    "レガシー": "legacy",
    "モダン": "modern",
    "オースブレイカー": "oathbreaker",
    "オールドスクール": "oldschool",
    "パウパー": "pauper",
    "パウパー統率者": "paupercommander",
    "パウパーコマンダー": "paupercommander",
    "ペニードレッドフル": "penny",
    "パイオニア": "pioneer",
    "プレモダン": "premodern",
    "スタンダード": "standard",
    "スタンダードブロール": "standardbrawl",
    "タイムレス": "timeless",
    "ヴィンテージ": "vintage",
    "プレDH": "predh",
    # commander variants
    "ハイランダー": "commander",  # highlander
    "リヴァイアサン": "commander",  # leviathan
    "タイニーリーダーズ": "commander",  # tiny leaders
    "アーチエネミー": "commander",  # archenemy
}


class DeckParser(ABC):
    """Abstract base deck parser.

    Parses input data into a Deck object.
    """
    @property
    def fmt(self) -> str:
        return self._metadata.get("format", "")

    def __init__(self, metadata: Json | None = None) -> None:
        self._metadata = dict(metadata) if metadata else {}
        self._state = _ParsingState()
        self._maindeck, self._sideboard = [], []
        self._commander, self._partner_commander, self._companion = None, None, None

    def _set_commander(self, card: Card) -> None:
        if not card.commander_suitable:
            _log.warning(f"'{card}' is not suitable for a commander role as per regular rules")

        if self._commander:
            if self._partner_commander:
                _log.warning("Partner commander already set")
                self._maindeck.append(card)
            else:
                if non_partner := from_iterable((self._commander, card), lambda c: not c.is_partner):
                    _log.warning(
                        f"Each partner commander should have a 'Partner' or 'Friends forever' "
                        f"keyword, '{non_partner}' doesn't")
                self._partner_commander = card
        else:
            self._commander = card

    def _derive_commander_from_sideboard(self) -> None:
        if self.fmt in COMMANDER_FORMATS and len(self._sideboard) in (1, 2) and all(
                c.commander_suitable for c in self._sideboard):
            for c in self._sideboard:
                self._set_commander(c)
            self._sideboard = []

    @classmethod
    def find_card(
            cls, name: str,
            set_and_collector_number: tuple[str, str] | None = None,
            scryfall_id="",
            oracle_id="",
            tcgplayer_id: int | None = None,
            cardmarket_id: int | None = None,
            mtgo_id: int | None = None,
            foreign=False) -> Card:
        """Find a MtG card designated by ``name`` and (optionally) other parameters.

        Raises:
            CardNotFound on failure

        Returns:
            a Card object
        """
        name = cls.sanitize_card_name(name)
        if set_and_collector_number:
            if card := find_by_collector_number(*set_and_collector_number):
                # don't assume set/collector number data is always correct in the input data
                if card.name == name:
                    return card
        if scryfall_id:
            if card := find_by_scryfall_id(scryfall_id):
                return card
        if oracle_id:
            if card := find_by_oracle_id(oracle_id):
                return card
        if tcgplayer_id is not None:
            if card := find_by_tcgplayer_id(tcgplayer_id):
                return card
        if cardmarket_id is not None:
            if card := find_by_cardmarket_id(cardmarket_id):
                return card
        if mtgo_id is not None:
            if card := find_by_mtgo_id(mtgo_id):
                return card
        if foreign:
            card = query_api_for_card(name, foreign=True)
        else:
            card = find_by_name(name)
        if not card:
            if SCRYFALL_MULTIFACE_SEPARATOR in name:
                truncated, *_ = name.split(SCRYFALL_MULTIFACE_SEPARATOR)
                card = find_by_name(truncated.strip())
            if not card:
                raise CardNotFound(f"Unable to find card {name!r}")
        return card

    @staticmethod
    def get_playset(card: Card, quantity: int) -> list[Card]:
        return [card] * quantity

    @staticmethod
    def sanitize_card_name(text: str) -> str:
        text = text.replace("’", "'").replace("‑", "-").replace("꞉", ":")
        if "/" in text:
            text = text.replace(" / ", SCRYFALL_MULTIFACE_SEPARATOR).replace(
                ARENA_MULTIFACE_SEPARATOR, SCRYFALL_MULTIFACE_SEPARATOR)
            # "Wear/Tear" ==> "Wear // Tear"
            # "Wear//Tear" ==> "Wear // Tear"
            # "Wear///Tear" ==> "Wear // Tear"
            text = re.sub(
                r'(?<=[a-zA-Z])/{1,3}(?=[a-zA-Z])', SCRYFALL_MULTIFACE_SEPARATOR, text)
        if "（" in text:  # for Japanese cards
            return remove_furigana(text)
        return text

    @abstractmethod
    def _pre_parse(self) -> None:
        """Do anything that is needed before parsing here (like obtaining a remote data from an
        input URL in scrapers).
        """
        raise NotImplementedError

    @abstractmethod
    def _parse_metadata(self) -> None:
        """Parse the input data for deck metadata.
        """
        raise NotImplementedError

    @abstractmethod
    def _parse_deck(self) -> None:
        """Parse the input data for deck data.
        """
        raise NotImplementedError

    def _build_deck(self) -> Deck | None:
        """Build a Deck object from the parsed deck data and return it.
        """
        return Deck(
            self._maindeck, self._sideboard, self._commander, self._partner_commander,
            self._companion, self._metadata)

    def parse(self, suppressed_errors=(InvalidDeck, CardNotFound)) -> Deck | None:  # override
        """Parse the input data for a Deck object.

        This happens in four distinct and defined stages:
            * pre-parsing
            * parsing metadata from the input data
            * parsing deck data from the input data
            * building a Deck object

        Args:
            suppressed_errors: a tuple of exceptions to suppress

        Returns:
            a Deck object or None
        """
        try:
            self._pre_parse()
            self._parse_metadata()
            self._parse_deck()
            return self._build_deck()
        except suppressed_errors as err:
            _log.warning(f"Parsing failed with: {err!r}")
            return None

    def update_metadata(self, **data: Any) -> None:
        self._metadata.update(data)

    def _update_fmt(self, fmt: str) -> None:
        fmt = fmt.strip().lower()
        fmt = SANITIZED_FORMATS.get(fmt, fmt)
        if fmt != self.fmt:
            if fmt in all_formats():
                self._metadata["format"] = fmt
            else:
                _log.warning(f"Irregular format: {fmt!r}")
                if self._metadata.get("format"):
                    del self._metadata["format"]
                self._metadata["irregular_format"] = fmt

    def _update_archetype_or_theme(self, name: str) -> None:
        if name.lower() in {a.value for a in Archetype}:
            self._metadata["archetype"] = name.lower()
        elif " " in name and any(t in {a.value for a in Archetype} for t in name.lower().split()):
            arch = from_iterable(name.lower().split(), lambda t: t in {a.value for a in Archetype})
            self._metadata["archetype"] = arch
            self._metadata["custom_theme"] = name
        elif name.replace(" ", "-").title() in THEMES:
            self._metadata["theme"] = name.replace(" ", "-").title()
        else:
            self._metadata["custom_theme"] = name

    @staticmethod
    def sanitize_metadata_deck_tags(deck_tags: list[str | Json]) -> list[str]:
        sanitized = []
        match deck_tags:
            case [*tags] if all(isinstance(t, str) for t in tags):
                return sorted({t.lower() for t in tags})
            case [*tags] if all(isinstance(t, dict) for t in tags):
                for tag in tags:
                    match tag:
                        case {"name": name} if isinstance(name, str):
                            sanitized.append(name.lower())
                        case {"Name": name} if isinstance(name, str):
                            sanitized.append(name.lower())
                        case {"tag": name} if isinstance(name, str):
                            sanitized.append(name.lower())
                        case {"Tag": name} if isinstance(name, str):
                            sanitized.append(name.lower())
                        case {"deck_tag": name} if isinstance(name, str):
                            sanitized.append(name.lower())
                        case {"deckTag": name} if isinstance(name, str):
                            sanitized.append(name.lower())
                        case {"Deck Tag": name} if isinstance(name, str):
                            sanitized.append(name.lower())
                        case _:
                            _log.warning(f"Unexpected format of deck metadata tag: {tag!r}")
            case _:
                _log.warning(f"Unexpected format of deck metadata tags: {deck_tags!r}")

        return sorted(set(sanitized))

    @staticmethod
    def derive_format_from_words(*words: str, use_japanese=False) -> str | None:
        words = {w.lower() for w in words}
        formats = {**SANITIZED_FORMATS, **JAPANESE_FORMATS} if use_japanese else SANITIZED_FORMATS
        if sanitized_fmt := from_iterable(formats, lambda k: k in words):
            return formats[sanitized_fmt]
        return from_iterable(all_formats(), lambda w: w in words)

    @staticmethod
    def derive_format_from_text(text: str, use_japanese=False) -> str | None:
        counts, text = [], text.lower()
        formats = {**SANITIZED_FORMATS, **JAPANESE_FORMATS} if use_japanese else SANITIZED_FORMATS
        for fmt_word in [*all_formats(), *formats]:
            count = text.count(fmt_word)
            if count:
                counts.append((fmt_word, count))
        if not counts:
            return None
        counts.sort(key=itemgetter(1), reverse=True)
        max_count = counts[0][1]
        top_words = [fw for fw, c in counts if c == max_count]
        top_words.sort(key=lambda w: len(w), reverse=True)
        fmt = top_words[0]
        return formats.get(fmt, fmt)


