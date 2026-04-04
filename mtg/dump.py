"""

    mtg.dump
    ~~~~~~~~
    Dump channels' deck data.

    @author: mazz3rr

"""
from collections.abc import Iterator
from pathlib import Path
from typing import Literal

from tqdm import tqdm

from mtg.constants import DECKS_DIR, PathLike
from mtg.data.common import load_channels, retrieve_ids
from mtg.data.structures import Channel
from mtg.deck.export import Exporter, FORMATS as EXPORT_FORMATS
from mtg.lib.common import logging_disabled
from mtg.lib.time import get_timestamp
from mtg.lib.files import getdir, sanitize_filename


def _dump_data_gen(
        channels: list[Channel],
        dstdir: Path) -> Iterator[tuple[Exporter | None, Path]]:
    seen_decks = {}
    for channel in channels:
        if title := channel.title:
            channel_dir = dstdir / f"{sanitize_filename(title)}_({channel.id})"
        else:
            channel_dir = dstdir /channel.id
        for video in channel.videos:
            for serialized_deck in video.decks:
                serialized_deck.metadata["video_url"] = video.url
                # FIXME: improve decks' equivalence (#426)
                deck = seen_decks.get(hash(serialized_deck), serialized_deck.deck())
                if deck:
                    seen_decks[hash(deck)] = deck
                    yield Exporter(deck), channel_dir
                else:
                    yield None, channel_dir


def dump_decks(
        dstdir: PathLike = "", fmt: Literal["arena", "forge", "json", "xmage"] = "forge") -> None:
    """Export all decks from all channels to ```dstdir``` in the format provided.
    """
    if fmt not in EXPORT_FORMATS:
        raise ValueError(f"Invalid dump format: {fmt!r}. Must be one of: {EXPORT_FORMATS}")
    timestamp = get_timestamp()
    dstdir = dstdir or DECKS_DIR / "yt" / timestamp
    dstdir = getdir(dstdir)
    chids = retrieve_ids()
    channels = [*tqdm(load_channels(*chids), total=len(chids), desc="Loading channels data...")]
    total = sum(len(ch.decks) for ch in channels)
    with logging_disabled():
        for exporter, channel_dir in tqdm(
                _dump_data_gen(channels, dstdir), total=total, desc="Exporting YT decks..."):
            if exporter:
                try:
                    match fmt:
                        case "arena":
                            exporter.to_arena(channel_dir)
                        case "forge":
                            exporter.to_forge(channel_dir)
                        case "json":
                            exporter.to_json(channel_dir)
                        case "xmage":
                            exporter.to_xmage(channel_dir)
                except OSError as err:
                    if "File name too long" in str(err):
                        pass
                    else:
                        raise
