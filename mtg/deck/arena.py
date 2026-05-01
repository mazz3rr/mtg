"""

    mtg.deck.arena
    ~~~~~~~~~~~~~~
    Parse Arena/MTGO decklist text format into Deck objects.

    @author: mazz3rr

"""
import logging
from typing import override

import regex as re

from mtg.constants import Json
from mtg.deck.core import ARENA_MULTIFACE_SEPARATOR, Deck, DeckParser
from mtg.lib.common import ParsingError
from mtg.lib.numbers import extract_int
from mtg.lib.text import get_hash, get_repr, sanitize_whitespace
from mtg.scryfall import Card, MULTIFACE_SEPARATOR as SCRYFALL_MULTIFACE_SEPARATOR

_log = logging.getLogger(__name__)


# define the first character class for card names:
# uppercase Latin, underscore, double-quote, or Japanese character
_FIRST_CHAR = r'[\p{Lu}_"\p{Han}\p{Hiragana}\p{Katakana}]'
# the rest: word, whitespace, punctuation, or Japanese scripts
_REST_CHARS = r'[\w\s\'\"&/,.!:_\-（）\u3000-\u303F\p{Han}\p{Hiragana}\p{Katakana}]*'


class PlaysetLine:
    """A line of text in MtG Arena decklist format that denotes a card playset.
    """
    # Regular: '4 トリックスター、ザレス・サン'
    PATTERN = re.compile(
        rf"^\d{{1,3}}\s?x?\s{_FIRST_CHAR}{_REST_CHARS}", re.UNICODE
    )
    # Inverted: 'トリックスター、ザレス・サン 4'
    INVERTED_PATTERN = re.compile(
        rf"^{_FIRST_CHAR}{_REST_CHARS}\sx?\s?\d{{1,3}}$", re.UNICODE
    )
    # With printings: '4 トリックスター、ザレス・サン (ZNR) 242'
    WITH_PRINTINGS_PATTERN = re.compile(
        rf"^\d{{1,3}}\s?x?\s{_FIRST_CHAR}{_REST_CHARS}\s+\([A-Za-z\d]{{3,6}}\)\s+[A-Za-z\d]{{1,6}}",
        re.UNICODE
    )

    @property
    def is_with_printings(self) -> bool:
        return self._is_with_printings

    @property
    def is_inverted(self) -> bool:
        return self._is_inverted

    @property
    def quantity(self) -> int:
        return self._quantity

    @property
    def name(self) -> str:
        return self._name.strip()

    @property
    def set_code(self) -> str:
        return self._set_code.lower()

    @property
    def collector_number(self) -> str:
        return self._collector_number

    def __init__(self, line: str) -> None:
        line = ArenaParser.normalize_card_name(line)
        regular_match = self.PATTERN.match(line)
        with_printings_match = self.WITH_PRINTINGS_PATTERN.match(line)
        inverted_match = self.INVERTED_PATTERN.match(line)
        self._is_with_printings = with_printings_match is not None
        self._is_inverted = inverted_match is not None
        if self.is_with_printings:
            matched_text = with_printings_match.group(0)
        elif self.is_inverted:
            matched_text = inverted_match.group(0)
        else:
            matched_text = regular_match.group(0)

        if self._is_inverted:
            rest, quantity = matched_text.rsplit(maxsplit=1)
        else:
            quantity, rest = matched_text.split(maxsplit=1)
        self._quantity = extract_int(quantity)
        if self.is_with_printings:
            self._name, rest = rest.split("(")
            self._name = self._name.strip()
            self._set_code, rest = rest.split(")")
            self._collector_number = rest.strip()
        else:
            self._name, self._set_code, self._collector_number = rest, "", ""
            if "(" in self.name and ")" in self.name and (self.name and self.name[-1].isdigit()):
                _log.warning(
                    f"{self.name!r} looks fishy for a card name. It seems like {line!r} decklist "
                    f"line is in format with printings and hasn't been recognized as such by the "
                    f"parser")
        self._name = self._name.replace(ARENA_MULTIFACE_SEPARATOR, SCRYFALL_MULTIFACE_SEPARATOR)

    def __repr__(self) -> str:
        pairs = [("quantity", self.quantity), ("name", self.name)]
        if self.is_with_printings:
            pairs += [("setcode", self.set_code), ("collector_number", self.collector_number)]
        return get_repr(self.__class__, *pairs)

    def to_playset(self) -> list[Card]:
        set_and_collector_number = (
            self.set_code, self.collector_number) if self.is_with_printings else None
        return DeckParser.get_playset(DeckParser.find_card(
            self.name, set_and_collector_number), self.quantity)


def _is_playset_line(line: str) -> bool:
    return bool(PlaysetLine.PATTERN.match(line)) or bool(
        PlaysetLine.INVERTED_PATTERN.match(line)) or bool(
        PlaysetLine.WITH_PRINTINGS_PATTERN.match(line))


def _is_inverted_playset_line(line: str) -> bool:
    return bool(PlaysetLine.INVERTED_PATTERN.match(line))


def is_empty(line: str) -> bool:
    return not line or line.isspace()


def _is_section_line(line: str, *sections: str) -> bool:
    sections = {*sections}
    sections.update({section.upper() for section in sections})
    sections.update({f"{section}:" for section in sections})
    pattern = re.compile(
        r"^\s*(" + "|".join(re.escape(section) for section in sections) + r")"
        r"(\s*[\[\(:]?\s*\d{1,3}[\]\)]?)?\s*:?$", re.IGNORECASE
    )
    return bool(pattern.match(line))


def _is_about_header_line(line: str) -> bool:
    return _is_section_line(line, "About")


def _is_name_line(line: str) -> bool:
    return line.startswith("Name ")


def _is_commander_header_line(line: str) -> bool:
    return _is_section_line(
        line,
        "Commander",
        "指挥官", # chinese simplified
        "指揮官", # chinese traditional
        "Commandant",  # french
        "Kommandeur",  # german
        "Comandante",  # italian, portuguese, spanish
        # japanese
        "統率者",
        "コマンダー",
        "사령관",  # korean
        "Командир",  # russian
    )


def _is_companion_header_line(line: str) -> bool:
    return _is_section_line(
        line,
        "Companion",
        "伙伴",  # chinese simplified
        "夥伴",  # chinese traditional
        "Compagnon",  # french
        "Gefährte",  # german
        "Compagno",  # italian
        "相棒",  # japanese
        "동료",  # korean
        "Companheiro",  # portuguese
        "Компаньон",  # russian
        "Compañero",  # spanish
    )


def _is_maindeck_header_line(line: str) -> bool:
    return _is_section_line(
        line,
        "Main",
        "Maindeck",
        "MainDeck",
        "Mainboard",
        "Deck",
        "Decklist",
        "Main Deck",
        "Main Board",
        "Deck List",
        "牌库",  # chinese simplified
        "牌庫",  # chinese traditional
        "Mazzo",  # italian
        "デッキ",  # japanese
        "덱",  # korean
        "Колода"  # russian
        "Mazo",  # spanish
        "Malet",  # french, portuguese
    )


def _is_sideboard_header_line(line: str) -> bool:
    return _is_section_line(
        line,
        "Side",
        "Sideboard",
        "Sidedeck",
        "Sidelist",
        "Side Board",
        "Side Deck",
        "Side List",
        "备牌",  # chinese simplified
        "備牌",  # chinese traditional
        "Réserve",  # french
        "サイドボード",  # japanese
        "사이드보드",  # korean
        "Резерв",  # russian
        "Banquillo",  # spanish
        "Reserva",  # spanish, portuguese
    )


def is_arena_line(line: str) -> bool:
    if _is_maindeck_header_line(line) or _is_sideboard_header_line(line):
        return True
    elif _is_commander_header_line(line) or _is_companion_header_line(line):
        return True
    elif _is_about_header_line(line) or _is_name_line(line):
        return True
    elif _is_playset_line(line):
        return True
    return False


class LinesParser:
    """Parse list of arbitrary text lines for multiple (or singular) Arena/MGTO decklists.

    In default mode this parser tries to find as many decklists as possible and doesn't assume
    that any decklist's section is going to be announced by a section header line. Instead,
    it looks at the size of consecutive playset lines' blocks it encounters and at sizes of gaps
    between them to determine the relevant states.

    In single-decklist mode it filters out anything that isn't a playset or section header line (
    including any possible gaps between them).
    """
    MIN_MAINDECK_SIZE = 6  # pretty arbitrary

    @property
    def decklists(self) -> list[str]:
        return self._decklists

    @property
    def _is_ready_for_closing(self) -> bool:
        return self._maindeck and self._maindeck != ["Deck"]

    @property
    def min_maindeck_size(self) -> int:
        if self._single_decklist_mode:
            return 1 if self._commander else 2
        return self.MIN_MAINDECK_SIZE

    def __init__(self, *lines: str) -> None:
        self._lines = lines
        self._buffer, self._blanks = [], 0
        self._metadata, self._commander, self._companion = [], [], []
        self._maindeck, self._sideboard = [], []
        self._decklists: list[str] = []
        self._single_decklist_mode = False

    def _flush(self, section: list[str]) -> None:
        self._buffer.reverse()
        while self._buffer:
            section.append(self._buffer.pop())

    def _reset(self) -> None:
        self._buffer, self._blanks = [], 0
        self._metadata, self._commander, self._companion = [], [], []
        self._maindeck, self._sideboard = [], []

    # also normalizes playsets order within sections
    def _concatenate(self) -> None:
        if self._commander:
            if self._commander[0] != "Commander":
                self._commander.insert(0, "Commander")
            self._commander = self._commander[:1] + sorted(self._commander[1:])
        if self._companion and self._companion[0] != "Companion":
            self._companion.insert(0, "Companion")
        if self._maindeck:
            if self._maindeck[0] != "Deck":
                self._maindeck.insert(0, "Deck")
            self._maindeck = self._maindeck[:1] + sorted(self._maindeck[1:])
        if self._sideboard:
            if self._sideboard[0] != "Sideboard":
                self._sideboard.insert(0, "Sideboard")
            self._sideboard = self._sideboard[:1] + sorted(self._sideboard[1:])
        if self._commander == ["Commander"]:
            self._commander = []
        if self._companion == ["Companion"]:
            self._companion = []
        if self._sideboard == ["Sideboard"]:
            self._sideboard = []
        concatenated = (
                self._metadata + self._commander + self._companion + self._maindeck
                + self._sideboard)
        self._reset()
        return self._decklists.append("\n".join(concatenated))

    def _get_lines_for_single_decklist_mode(self) -> list[str]:
        lines = [l for l in self._lines if is_arena_line(l)]
        regular, inverted = [], []
        for line in lines:
            if _is_playset_line(line):
                if _is_inverted_playset_line(line):
                    inverted.append(line)
                else:
                    regular.append(line)
        if len(regular) >= len(inverted):
            for l in inverted:
                lines.remove(l)
        else:
            for l in regular:
                lines.remove(l)
        return lines

    def _handle_header_line(self, header: str, section: list[str]) -> None:
        if header not in section:
            if self._is_ready_for_closing:
                self._finish_decklist()
            else:
                self._finish_section()
            section.append(header)

    def parse(self, single_decklist_mode=False) -> list[str]:
        self._single_decklist_mode = single_decklist_mode
        lines = self._get_lines_for_single_decklist_mode() if single_decklist_mode else self._lines
        self._reset()
        self._decklists = []
        last_line = None

        for line in lines:
            if is_arena_line(line):
                self._blanks = 0
            if _is_about_header_line(line):
                self._handle_header_line("About", self._metadata)
            elif _is_name_line(line):
                if self._metadata == ["About"]:
                    self._metadata.append(line)
            elif _is_commander_header_line(line):
                self._handle_header_line("Commander", self._commander)
            elif _is_companion_header_line(line):
                self._handle_header_line("Companion", self._companion)
            elif _is_maindeck_header_line(line):
                self._handle_header_line("Deck", self._maindeck)
            elif _is_sideboard_header_line(line):
                if "Sideboard" not in self._sideboard:
                    if last_line and is_arena_line(last_line):
                        self._finish_section()
                    self._sideboard.append("Sideboard")
            elif _is_playset_line(line):
                # handle cases like:
                # Commander
                # 1 Some Commander Card
                # 1 Some Maindeck Card (without prior section separation)
                if (self._commander == ["Commander"]
                        and self._companion != ["Companion"]
                        and self._maindeck != ["Deck"]
                        and self._sideboard != ["Sideboard"]):
                    if len(self._buffer) == 1:
                        self._flush(self._commander)
                # handle cases like:
                # Companion
                # 1 Some Companion Card
                # 1 Some Maindeck Card (without prior section separation)
                elif (self._companion == ["Companion"]
                        and self._commander != ["Commander"]
                        and self._maindeck != ["Deck"]
                        and self._sideboard != ["Sideboard"]):
                    if len(self._buffer) == 1:
                        self._flush(self._companion)
                self._buffer.append(line)
            else:
                self._blanks += 1
                self._finish_section()

            last_line = line

        self._finish_decklist()
        self._single_decklist_mode = False
        return self._decklists

    def _finish_section(self) -> None:
        if self._buffer:
            if self._is_ready_for_closing:
                self._flush(self._sideboard)
            else:
                if not self._maindeck == ["Deck"]:
                    if len(self._buffer) == 1 and self._companion == ["Companion"]:
                        self._flush(self._companion)
                    elif (
                            len(self._buffer) <= 2
                            and (self._commander == ["Commander"] or not self._commander)
                    ):
                        if not self._commander:
                            self._commander = ["Commander"]
                        self._flush(self._commander)
                elif len(self._buffer) >= self.min_maindeck_size:
                    self._flush(self._maindeck)
                else:
                    self._buffer = []
        # so, we track number of consecutive "trash" lines and reset state after each gap longer
        # than two lines - that way we still can track ambiguous sections (not heralded by
        # section headers) based on their size (provided they're not separated by gaps longer
        # than 2 thrash lines) and at the same time we're able to weed out most cases of false
        # positives (e.g. lines like "Episode 274" - which mimic playset lines in inverted form)
        # - because they get discarded after each too-long a gap
        elif self._blanks > 2:
            self._finish_decklist()

    def _finish_decklist(self) -> None:
        if self._buffer:
            if self._is_ready_for_closing:
                self._flush(self._sideboard)
            elif len(self._buffer) >= self.min_maindeck_size:
                self._flush(self._maindeck)
            else:
                self._buffer = []
        if self._is_ready_for_closing:
            self._concatenate()
        else:
            self._reset()


def is_arena_decklist(decklist: str) -> bool:
    return all(is_arena_line(l) or is_empty(l) for l in decklist.splitlines())


class MalformedDecklist(ParsingError):
    """Raised on a not correctly formed Arena/MTGO text decklist being parsed as one.
    """


def normalize_decklist(decklist: str) -> str:
    """Return decklist with all section headers present and playset order within sections sorted
    alphabetically.
    """
    lines = [sanitize_whitespace(l) for l in decklist.splitlines()]
    decklists = LinesParser(*lines).parse(single_decklist_mode=True)
    if not decklists:
        raise MalformedDecklist("Not a correctly formed Arena/MTGO text decklist")
    return decklists[0]


_cached_decks: dict[str, Deck] = {}


class ArenaParser(DeckParser):
    """Parse a text decklist in Arena/MTGO format into a Deck object.
    """
    MAX_CARD_QUANTITY = 59

    @property
    def max_card_quantity(self) -> int:
        return 99 if self._commander is not None else self.MAX_CARD_QUANTITY

    def __init__(self, decklist: str, metadata: Json | None = None) -> None:
        super().__init__(metadata)
        self._decklist = decklist
        self._decklist_hash: str | None = None

    @override
    def _pre_parse(self) -> None:
        self._decklist = normalize_decklist(self._decklist)
        self._decklist_hash = get_hash(self._decklist, 40, sep="-")
        self._is_cached = self._decklist_hash in _cached_decks

    @override
    def _parse_input_for_metadata(self) -> None:
        pass

    # last safeguard against lines that mimicked decklist lines successfully enough
    # not to be weeded out at this point
    def _quantity_exceeded(self, playset: list[Card]) -> bool:
        max_quantity, word = self.max_card_quantity, "card"
        if self._state.is_commander:
            max_quantity, word = 1, "commander card"
        elif self._state.is_companion:
            max_quantity, word = 1, "companion card"
        if len(playset) > max_quantity:
            _log.warning(
                f"Quantity too high ({len(playset)}) for {word} {playset[0].name!r}. "
                f"Skipping...")
            return True
        return False

    @override
    def _parse_input_for_decklist(self) -> None:
        if self._is_cached:
            return
        for line in self._decklist.splitlines():
            if _is_maindeck_header_line(line):
                self._state.shift_to_maindeck()
            elif _is_sideboard_header_line(line):
                self._state.shift_to_sideboard()
            elif _is_commander_header_line(line):
                self._state.shift_to_commander()
            elif _is_companion_header_line(line):
                self._state.shift_to_companion()
            elif _is_name_line(line):
                self._metadata["name"] = line.removeprefix("Name ")
            elif _is_playset_line(line):
                if self._state.is_idle:
                    self._state.shift_to_maindeck()

                playset = PlaysetLine(line).to_playset()
                if self._quantity_exceeded(playset):
                    continue

                if self._state.is_sideboard:
                    self._sideboard.extend(playset)
                elif self._state.is_commander:
                    card = playset[0]
                    self._set_commander(card)
                elif self._state.is_companion:
                    self._companion = playset[0]
                elif self._state.is_maindeck:
                    self._maindeck.extend(playset)

    @override
    def _build_deck(self) -> Deck | None:
        if self._is_cached:
            deck = _cached_decks[self._decklist_hash]
            deck.replace_metadata(self._metadata)
            return deck
        deck = super()._build_deck()
        if deck:
            _cached_decks[self._decklist_hash] = deck
        return deck
