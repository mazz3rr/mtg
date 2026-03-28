"""
    mtg.deck.export
    ~~~~~~~~~~~~~~~
    Export deck data to various file formats (and import it back).

    @author: mazz3rr

"""
import logging
from pathlib import Path
from typing import Literal

from mtg.constants import OUTPUT_DIR, PathLike
from mtg.deck.arena import ArenaParser, IllFormedArenaDecklist, is_arena_decklist
from mtg.deck.core import CardNotFound, Deck, DeckParser, Mode
from mtg.lib.common import ParsingError, from_iterable
from mtg.lib.files import getdir, getfile, sanitize_filename, truncate_path
from mtg.lib.json import from_json as deserialize_json, to_json
from mtg.scryfall import Card, aggregate, set_cards

_log = logging.getLogger(__name__)
FORMATS = "arena", "forge", "json", "xmage"


class Exporter:
    """Export a deck to various text file formats.
    """
    NAME_SEP = "_"
    FORGE_LINE_TEMPLATE = "{} {}|{}"
    XMAGE_LINE_TEMPLATE = "{} [{}:{}] {}"
    XMAGE_SIDEBOARD_LINE_TEMPLATE = "SB: {} [{}:{}] {}"

    @property
    def name(self) -> str:
        if self._deck.name:
            return self._normalize_name(self._deck.name).replace(self.NAME_SEP, " ")
        return self._filename_core.replace(self.NAME_SEP, " ")

    def __init__(self, deck: Deck, filename="") -> None:
        """Initialize.

        Args:
            deck: deck to export
            filename: optionally, a custom filename (if not provided a name based on this deck's data and metadata is constructed)
        """
        self._deck = deck
        self._filename_core = self._build_filename_core()
        self._filename = filename or self._build_filename()

    @classmethod
    def _normalize_name(cls, name: str) -> str:
        name = name.replace(" ", cls.NAME_SEP).replace("-", cls.NAME_SEP)
        name = cls.NAME_SEP.join([p.title() for p in name.split(cls.NAME_SEP)])
        name = name.replace(f"5c{cls.NAME_SEP}", f"5C{cls.NAME_SEP}").replace(
            f"4c{cls.NAME_SEP}", f"4C{cls.NAME_SEP}")
        name = name.replace(f"Five{cls.NAME_SEP}Color{cls.NAME_SEP}", f"5C{cls.NAME_SEP}").replace(
            f"Four{cls.NAME_SEP}Color{cls.NAME_SEP}", f"4C{cls.NAME_SEP}")
        return name

    @classmethod
    def _remove_trailing_name_sep(cls, text: str) -> str:
        if text.endswith(cls.NAME_SEP):
            return text[:-len(cls.NAME_SEP)]
        return text

    def _build_filename_core(self) -> str:
        core = ""
        # color
        if len(self._deck.color.value) == 1:
            core += f"Mono{self.NAME_SEP}{self._deck.color.name.title()}{self.NAME_SEP}"
        elif len(self._deck.color.value) == 4:
            core += f"4C{self.NAME_SEP}"
        elif len(self._deck.color.value) == 5:
            core += f"5C{self.NAME_SEP}"
        else:
            core += f"{self._deck.color.name.title()}{self.NAME_SEP}"
        # theme
        if self._deck.theme:
            core += f"{self._deck.theme}{self.NAME_SEP}"
        # archetype
        core += f"{self._deck.archetype.name.title()}{self.NAME_SEP}"
        # set
        if set_code := self._deck.latest_set:
            core += f"{set_code.upper()}{self.NAME_SEP}"
        return self._remove_trailing_name_sep(core)

    def _build_filename(self) -> str:
        name = ""
        # format
        if self._deck.format:
            name += f"{self._deck.format}{self.NAME_SEP}"
        # date
        if date := self._deck.metadata.get("date"):
            date = date.strftime("%Y%m%d")
            name += f"{date}{self.NAME_SEP}{self.NAME_SEP}"
        # prefix (source/author)
        source = self._deck.source.replace(".", self.NAME_SEP)
        if self._deck.is_meta_deck and source:
            prefix = source
        else:
            prefix = self._deck.metadata.get("author", "") or source
        name += f"{prefix}{self.NAME_SEP}" if prefix else ""
        # actual name
        if self._deck.name:
            name += f"{self._normalize_name(self._deck.name)}{self.NAME_SEP}"
        # mode
        if mode := self._deck.metadata.get("mode"):
            if mode in {m.value for m in Mode}:
                name += f"{mode}{self.NAME_SEP}"
        # core:
        core = self._filename_core
        core = f"({core})" if name else core
        name += f"{core}{self.NAME_SEP}"
        # meta
        if self._deck.is_meta_deck:
            name += f"Meta{self.NAME_SEP}"
            meta = self._deck.metadata["meta"]
            if meta_place := meta.get("place"):
                name += f"#{str(meta_place).zfill(2)}{self.NAME_SEP}"
        # event
        event_name = self._deck.metadata.get("event_name", "") or self._deck.metadata.get(
            "event", {})
        if event_name and isinstance(event_name, dict):
            event_name = event_name.get("name", "")
        if event_name and not self._deck.is_meta_deck:
            name += f"Event{self.NAME_SEP}{event_name}{self.NAME_SEP}"
        return sanitize_filename(self._remove_trailing_name_sep(name))

    def to_arena(self, dstdir: PathLike = "", with_printings=True) -> None:
        """Export deck to a MTGA deckfile text format (as a .txt file).

        Args:
            dstdir: optionally, the destination directory (if not provided CWD is used)
            with_printings: optionally, include the card's set and collector number (default: True)
        """
        dstdir = dstdir or OUTPUT_DIR / "arena"
        dstdir = getdir(dstdir)
        dst = dstdir / f"{self._filename}.txt"
        dst = Path(truncate_path(str(dst)))
        _log.info(f"Exporting deck to: '{dst}'...")
        dst.write_text(
            self._deck.decklist_with_printings if with_printings else self._deck.decklist, encoding="utf-8")

    def to_json(self, dstdir: PathLike = "", with_printings=True) -> None:
        """Export deck to a .json file.

        JSON exported to file holds the whole decklist (in simplified or detailed format) and not
        only IDs.

        Args:
            dstdir: optionally, the destination directory (if not provided CWD is used)
            with_printings: optionally, include in decklist the card's set and collector number (default: True)
        """
        data = {
            "metadata": self._deck.metadata,
            "decklist": self._deck.decklist_with_printings if with_printings else self._deck.decklist,
        }
        data = to_json(data, sort_dictionaries=True)
        dstdir = dstdir or OUTPUT_DIR / "json"
        dstdir = getdir(dstdir)
        dst = dstdir / f"{self._filename}.json"
        dst = Path(truncate_path(str(dst)))
        _log.info(f"Exporting deck to: '{dst}'...")
        dst.write_text(data, encoding="utf-8")

    @classmethod
    def _to_forge_line(cls, playset: list[Card]) -> str:
        card = playset[0]
        return cls.FORGE_LINE_TEMPLATE.format(len(playset), card.first_face_name, card.set.upper())

    def _get_forge_metadata_lines(self) -> list[str]:
        lines = ["[metadata]"]
        lines += [f"Name={self.name}"]
        if self._deck.format:
            lines += [f"Format={self._deck.format}"]
        if author :=self._deck.metadata.get("author"):
            lines += [f"Author={author}"]
        if date := self._deck.metadata.get("date"):
            lines += [f"Date={date}"]
        if self._deck.source:
            lines += [f"Source={self._deck.source}"]
        if url := self._deck.metadata.get("video_url") or self._deck.metadata.get("url"):
            lines += [f"URL={url}"]
        return lines

    def _build_forge(self) -> str:
        commander = [
            self._to_forge_line(playset) for playset in
            aggregate(self._deck.commander).values()] if self._deck.commander else []
        if self._deck.partner_commander:
            commander += [self._to_forge_line(playset) for playset in aggregate(
                self._deck.partner_commander).values()]
        maindeck = [
            self._to_forge_line(playset) for playset in aggregate(*self._deck.maindeck).values()]
        sideboard = [
            self._to_forge_line(playset) for playset in
            aggregate(*self._deck.sideboard).values()] if self._deck.sideboard else []
        lines = self._get_forge_metadata_lines()
        if commander:
            lines += ["[Commander]", *commander]
        lines += ["[Main]", *maindeck]
        if sideboard:
            lines += ["[Sideboard]", *sideboard]
        return "\n".join(lines)

    def to_forge(self, dstdir: PathLike = "") -> None:
        """Export deck to a Forge MTG deckfile format (.dck).

        Args:
            dstdir: optionally, the destination directory (if not provided CWD is used)
        """
        dstdir = dstdir or OUTPUT_DIR / "dck"
        dstdir = getdir(dstdir)
        dst = dstdir / f"{self._filename}.dck"
        dst = Path(truncate_path(str(dst)))
        _log.info(f"Exporting deck to: '{dst}'...")
        dst.write_text(self._build_forge(), encoding="utf-8")

    @classmethod
    def _to_xmage_line(cls, playset: list[Card], sideboard=False) -> str:
        card = playset[0]
        template = cls.XMAGE_SIDEBOARD_LINE_TEMPLATE if sideboard else cls.XMAGE_LINE_TEMPLATE
        return template.format(
            len(playset), card.set.upper(), card.collector_number, card.first_face_name)

    def _get_xmage_metadata_lines(self) -> list[str]:
        lines = [f"NAME:{self.name}"]
        if self._deck.format:
            lines += [f"FORMAT:{self._deck.format}"]
        if author :=self._deck.metadata.get("author"):
            lines += [f"AUTHOR:{author}"]
        if date := self._deck.metadata.get("date"):
            lines += [f"DATE:{date}"]
        if self._deck.source:
            lines += [f"SOURCE:{self._deck.source}"]
        if url := self._deck.metadata.get("video_url") or self._deck.metadata.get("url"):
            lines += [f"URL:{url}"]
        return lines

    def _build_xmage(self) -> str:
        lines = self._get_xmage_metadata_lines()
        lines += [
            self._to_xmage_line(playset) for playset in aggregate(*self._deck.maindeck).values()]
        commander = [
            self._to_xmage_line(playset, sideboard=True) for playset in
            aggregate(self._deck.commander).values()] if self._deck.commander else []
        if self._deck.partner_commander:
            commander += [self._to_xmage_line(playset, sideboard=True) for playset in aggregate(
                self._deck.partner_commander).values()]
        if commander:
            lines += commander
        else:
            lines += [
                self._to_xmage_line(playset, sideboard=True) for playset in
                aggregate(*self._deck.sideboard).values()] if self._deck.sideboard else []
        return "\n".join(lines)

    def to_xmage(self, dstdir: PathLike = "") -> None:
        """Export deck to a XMage deckfile format (.dck).

        Args:
            dstdir: optionally, the destination directory (if not provided CWD is used)
        """
        dstdir = dstdir or OUTPUT_DIR / "dck"
        dstdir = getdir(dstdir)
        dst = dstdir / f"{self._filename}.dck"
        dst = Path(truncate_path(str(dst)))
        _log.info(f"Exporting deck to: '{dst}'...")
        dst.write_text(self._build_xmage(), encoding="utf-8")


def from_arena(path: PathLike) -> Deck:
    """Import deck from a MTG Arena deckfile format (.txt).

    Args:
        path: path to an Arena deck file
    """
    file = getfile(path, ".txt")
    decklist = file.read_text(encoding="utf-8")
    if not is_arena_decklist(decklist):
        raise IllFormedArenaDecklist(f"Not an MTG Arena deck file: '{file}'")
    return ArenaParser(decklist).parse(suppressed_errors=())


def _parse_forge_line(line: str) -> list[Card]:
    quantity, rest = line.split(maxsplit=1)
    if "|" in rest:
        name, set_code, *_ = rest.strip().split("|")
        cards = set_cards(set_code)
        card = from_iterable(cards, lambda c: c.first_face_name == name)
        if not card:
            _log.warning(f"Card {name!r} not found in set {set_code!r}")
        card = DeckParser.find_card(name)
    else:
        name = rest.strip()
        card = DeckParser.find_card(name)

    if not card:
        raise CardNotFound(f"Unable to find {name!r}")
    return DeckParser.get_playset(card, int(quantity))


def from_forge(path: PathLike) -> Deck:
    """Import a deck from a Forge MTG deckfile format (.dck).

    Args:
        path: path to a .dck file
    """
    file = getfile(path, ".dck")
    commander, maindeck, sideboard, metadata = [], [], [], {}
    metadata_on, commander_on, maindeck_on, sideboard_on = False, False, False, False
    for line in file.read_text(encoding="utf-8").splitlines():
        if line == "[metadata]":
            metadata_on = True
            continue
        elif line == "[Commander]":
            metadata_on, commander_on = False, True
            continue
        elif line == "[Main]":
            metadata_on, commander_on, maindeck_on = False, False, True
            continue
        elif line == "[Sideboard]":
            maindeck_on, sideboard_on = False, True
            continue
        elif not line:
            continue

        if metadata_on:
            if line.startswith("Name="):
                metadata["name"] = line.removeprefix("Name=")
            elif line.startswith("Format="):
                metadata["format"] = line.removeprefix("Format=")
            elif line.startswith("Author="):
                metadata["author"] = line.removeprefix("Author=")
            elif line.startswith("Date="):
                metadata["date"] = line.removeprefix("Date=")
            elif line.startswith("Source="):
                metadata["source"] = line.removeprefix("Source=")
            elif line.startswith("URL="):
                metadata["url"] = line.removeprefix("URL=")
        elif commander_on:
            commander.append(_parse_forge_line(line)[0])
        elif maindeck_on:
            maindeck += _parse_forge_line(line)
        elif sideboard_on:
            sideboard += _parse_forge_line(line)

    deck = Deck(maindeck, sideboard, *commander, metadata=metadata)
    if not deck:
        raise ParsingError(f"Unable to parse '{path}' into a deck")
    return deck


def _parse_xmage_line(line: str) -> list[Card]:
    quantity, set_part, name = line.split(maxsplit=2)
    set_code, collector_number = set_part[1:-1].split(":")
    card = DeckParser.find_card(name, (set_code, collector_number))
    if not card:
        raise CardNotFound(f"Unable to find {name!r}")
    return DeckParser.get_playset(card, int(quantity))


def from_xmage(path: PathLike) -> Deck:
    """Import a deck from a XMage deckfile format (.dck).

    Args:
        path: path to a .dck file
    """
    file = getfile(path, ".dck")
    commander, maindeck, sideboard, metadata = [], [], [], {}
    for line in file.read_text(encoding="utf-8").splitlines():
        if line.startswith("NAME:"):
            metadata["name"] = line.removeprefix("NAME:")
        elif line.startswith("FORMAT:"):
            metadata["format"] = line.removeprefix("FORMAT:")
        elif line.startswith("AUTHOR:"):
            metadata["author"] = line.removeprefix("AUTHOR:")
        elif line.startswith("DATE:"):
            metadata["date"] = line.removeprefix("DATE:")
        elif line.startswith("SOURCE:"):
            metadata["source"] = line.removeprefix("SOURCE:")
        elif line.startswith("URL:"):
            metadata["url"] = line.removeprefix("URL:")
        elif line.startswith("LAYOUT "):
            continue  # ignore XMage internal specifics
        else:
            if line.startswith("SB: "):
                sideboard += _parse_xmage_line(line.removeprefix("SB: "))
            else:
                maindeck += _parse_xmage_line(line)

    if len(sideboard) in (1, 2):
        commander, sideboard = sideboard, commander

    deck = Deck(maindeck, sideboard, *commander, metadata=metadata)
    if not deck:
        raise ParsingError(f"Unable to parse '{path}' into a deck")
    return deck


def from_json(path: PathLike) -> Deck:
    """Import deck from a JSON deckfile.

    Args:
        path: path to a JSON deckfile
    """
    file = getfile(path, ".json")
    data = deserialize_json(file.read_text(encoding="utf-8"))
    return ArenaParser(data["decklist"], data["metadata"]).parse()


def _convert_file(
        file: Path, fmt: Literal["arena", "forge", "json", "xmage"], dst_dir: Path) -> None:
    text = file.read_text(encoding="utf-8")
    if text[0] == "{":
        deck = from_json(file)
    elif "[Main]" in text or "[main]" in text:
        deck = from_forge(file)
    elif is_arena_decklist(text):
        deck = from_arena(file)
    else:
        deck = from_xmage(file)
    if fmt == "arena":
        Exporter(deck, file.stem).to_arena(dst_dir)
    elif fmt == "forge":
        if file.suffix.lower() == ".dck" and dst_dir == file.parent:  # don't overwrite original
            name = f"{file.stem}_forge"
        else:
            name = file.stem
        Exporter(deck, name).to_forge(dst_dir)
    elif fmt == "json":
        Exporter(deck, file.stem).to_json(dst_dir)
    else:
        if file.suffix.lower() == ".dck" and dst_dir == file.parent:  # don't overwrite original
            name = f"{file.stem}_xmage"
        else:
            name = file.stem
        Exporter(deck, name).to_xmage(dst_dir)


def convert(
        src_path: PathLike, fmt: Literal["arena", "forge", "json", "xmage"],
        dst_dir: PathLike = "") -> None:
    """Convert deckfile(s) to the specified format.

    Printings-specific card data may not be preserved during conversion.

    Args:
        src_path: source path to a deckfile or directory containing them
        fmt: conversion format
        dst_dir: optionally, a destination directory
    """
    if fmt not in FORMATS:
        raise ValueError(f"Invalid conversion format: {fmt!r}. Must be one of: {FORMATS}")
    file = getfile(src_path, ".dck", ".json", ".txt", suppress_errors=True)
    if file:
        dst_dir = getdir(dst_dir) if dst_dir else file.parent
        _convert_file(file, fmt, dst_dir)
    else:
        folder = getdir(src_path, create_missing=False)
        root = getdir(dst_dir) if dst_dir else folder
        deckfiles = [
            f for f in folder.rglob("*")
            if f.is_file() and f.suffix.lower() in {".dck", ".json", ".txt"}]
        for deckfile in deckfiles:
            dst_dir = (root / deckfile.relative_to(folder)).parent
            _convert_file(deckfile, fmt, dst_dir)
