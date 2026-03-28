"""

    mtg.gstate
    ~~~~~~~~~~
    Global state managers.

    Note: 'global' is not a valid name for a Python module (hence 'gstate').

    @author: mazz3rr

"""
import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable

from mtg.constants import OUTPUT_DIR
from mtg.lib.check_type import type_checker
from mtg.lib.files import getfile
from mtg.lib.scrape.core import throttle_with_countdown

_log = logging.getLogger(__name__)

CHANNELS_DIR = OUTPUT_DIR / "channels"
DECKLISTS_FILE = CHANNELS_DIR / "decklists.json"
DECKLISTS_WITH_PRINTINGS_FILE = CHANNELS_DIR / "decklists_with_printings.json"
FAILED_URLS_FILE = CHANNELS_DIR / "failed_urls.json"


class _Singleton(ABC):
    _initialized = False

    def __init__(self) -> None:
        if not self.__class__._initialized:
            # pilfered this neat singleton solution from: https://stackoverflow.com/a/64545504/4465708
            self.__class__.__new__ = lambda _: self
            # init state
            self.reset()
            self.__class__._initialized = True

    @abstractmethod
    def reset(self) -> None:
        raise NotImplementedError


class UrlsStateManager(_Singleton):
    """State manager for already scraped and failed URLs.

    Scraped URLs are stored within deck data and their initial state is loaded from there by third
    parties and only injected here via 'update_scraped()' method. This object tracks dynamically
    changing state afterward.

    Failed URLs on the other hand are stored in their own dedicated file and both this file's
    management and the dynamic state of the URLs are a responsibility of this object.
    """
    @property
    def current_channel(self) -> str:
        return self.__current_channel

    @current_channel.setter
    @type_checker(str, is_method=True)
    def current_channel(self, value: str) -> None:
        self.__current_channel = value

    @property
    def current_video(self) -> str:
        return self.__current_video

    @current_video.setter
    @type_checker(str, is_method=True)
    def current_video(self, value: str) -> None:
        self.__current_video = value

    @property
    def ignore_failed(self) -> bool:
        return self.__ignore_failed

    @ignore_failed.setter
    @type_checker(bool, is_method=True)
    def ignore_failed(self, value: bool) -> None:
        self.__ignore_failed = value

    @property
    def ignore_scraped(self) -> bool:
        return self.__ignore_scraped

    @ignore_scraped.setter
    @type_checker(bool, is_method=True)
    def ignore_scraped(self, value: bool) -> None:
        self.__ignore_scraped = value

    @property
    def ignore_scraped_within_current_video(self) -> bool:
        return self.__ignore_scraped_within_current_video

    @ignore_scraped_within_current_video.setter
    @type_checker(bool, is_method=True)
    def ignore_scraped_within_current_video(self, value: bool) -> None:
        self.__ignore_scraped_within_current_video = value

    @property
    def failed(self) -> dict[str, set[str]]:
        return dict(self._failed)

    @property
    def failed_count(self) -> int:
        return sum(len(v) for v in self._failed.values())

    def _get_scraped(self, channel_id: str, video_id="") -> set[str]:
        if channel_id and video_id:
            return self._scraped.get(f"{channel_id}/{video_id}", set())
        return self._scraped.get(channel_id, set())

    # used by the ScrapingSession/Channel to load initial global state from disk
    def update_scraped(self, data: dict[str, set[str]]) -> None:
        self._scraped.update(
            {k: {url.removesuffix("/").lower() for url in v} for k, v in data.items()})

    def update_failed(self, data: dict[str, set[str]]) -> None:
        self._failed.update(
            {k: {url.removesuffix("/").lower() for url in v} for k, v in data.items()})

    # TODO: use db
    def load_failed(self) -> None:
        if self._initial_failed_count:
            _log.warning("Failed URLs already loaded")
            return
        src = getfile(FAILED_URLS_FILE)
        self._failed = {k: set(v) for k, v in json.loads(src.read_text(
                encoding="utf-8")).items()}
        self._initial_failed_count = self.failed_count
        _log.info(
            f"Loaded {self._initial_failed_count:,} decklist URL(s) that previously failed from "
            f"the global repository")

    def dump_failed(self) -> None:
        dst = Path(FAILED_URLS_FILE)
        dst.write_text(
            json.dumps({k: sorted(v) for k, v in self.failed.items()}, indent=4,
                       ensure_ascii=False), encoding="utf-8")
        delta = self.failed_count - self._initial_failed_count
        _log.info(f"Total pool of previously failed decklist URLs changed by: {delta:,}")

    # used by the scraping session on finish
    def reset(self) -> None:  # override
        self._scraped: dict[str, set[str]] = {}  # maps 'channel_id/video_id' path to set of URLs
        self._failed: dict[str, set[str]] = {}  # maps 'channel_id' to set of URLs
        self.current_channel, self.current_video = "", ""
        self.ignore_failed = False
        self.ignore_scraped, self.ignore_scraped_within_current_video = False, False
        self._initial_failed_count = 0

    # used by URL-based scrapers
    def add_scraped(self, url: str) -> None:
        url = url.removesuffix("/").lower()
        self._scraped.setdefault(self.current_channel, set()).add(url)
        self._scraped.setdefault(f"{self.current_channel}/{self.current_video}", set()).add(url)
        # if present, remove successfully scraped URL from failed (useful when re-scraping)
        if self.current_channel in self._failed:
            self._failed[self.current_channel].discard(url)

    def add_failed(self, url: str) -> None:
        self._failed.setdefault(self.current_channel, set()).add(url.removesuffix("/").lower())

    def prune_failed(self, keeper_chids: set[str]) -> None:
        """Prune failed URLs in the global repository that are not in the specified channels.
        """
        channels_count, urls_count = 0, 0
        new_failed = {}
        for k in self._failed:
            if k in keeper_chids:
                new_failed[k] = self._failed[k]
            else:
                urls_count += len(self._failed[k])
                channels_count += 1
        self._failed = new_failed
        _log.info(
            f"Removed failed URLs data for {channels_count:,} channel(s) ({urls_count:,} URL(s) "
            f"in total)")

    def _is_scraped_within(self, url: str, channel_id="", video_id="") -> bool:
        if self.ignore_scraped:
            return False
        return url.removesuffix("/").lower() in self._get_scraped(channel_id, video_id)

    def is_scraped(self, url: str) -> bool:
        url = url.removesuffix("/").lower()
        if self.ignore_scraped_within_current_video and self._is_scraped_within(
            url, self.current_channel, self.current_video):
            return False
        return self._is_scraped_within(url, self.current_channel)

    def is_failed(self, url: str) -> bool:
        if self.ignore_failed:
            return False
        return url.removesuffix("/").lower() in self._failed.get(self.current_channel, set())


# FIXME: tracking printings-specific decklists while using Oracle Bulk Data is of no use
class DecklistsStateManager(_Singleton):
    """State manager for decklists stored separately from deck data.

    MTGO/Arena text format decklists mapped to their hash digest IDs are stored in two files: one
    for regular decklists and the second for decklists in detailed form (i.e. where each line
    specifies also a set code and card's collector number - this detail makes a deck specific in
    terms of card prints).

    Storing decklists in that way makes keeping only hash digests IDs (instead of whole
    decklists) in the deck data possible and, thus, cuts down data bloat due to decklists
    duplication across the scraped decks.
    """
    @property
    def regular(self) -> dict[str, str]:
        return dict(self._regular)

    @property
    def with_printings(self) -> dict[str, str]:
        return dict(self._with_printings)

    @property
    def is_loaded(self) -> bool:
        return self._is_loaded

    def reset(self) -> None:  # override
        self._regular: dict[str, str] = {}
        self._with_printings: dict[str, str] = {}
        self._initial_regular_count, self._initial_with_printings_count = 0, 0
        self._is_loaded = False

    def load(self) -> None:
        if self.is_loaded:
            _log.warning("Decklists already loaded")
            return
        regular_src, with_printings_src = getfile(DECKLISTS_FILE), getfile(
            DECKLISTS_WITH_PRINTINGS_FILE)
        self._regular = json.loads(regular_src.read_text(encoding="utf-8"))
        self._initial_regular_count = len(self._regular)
        _log.info(
            f"Loaded {self._initial_regular_count:,} regular decklist(s) from the global "
            f"repository")
        self._with_printings = json.loads(with_printings_src.read_text(encoding="utf-8"))
        self._initial_with_printings_count = len(self._with_printings)
        self._is_loaded = True
        _log.info(
            f"Loaded {self._initial_with_printings_count:,} decklist(s) with printings from the "
            f"global repository")

    def dump(self) -> None:
        regular_dst, with_printings_dst = Path(DECKLISTS_FILE), Path(DECKLISTS_WITH_PRINTINGS_FILE)
        regular_count, with_printings_count = len(self._regular), len(self._with_printings)
        _log.info(f"Dumping {regular_count:,} decklist(s) to '{regular_dst}'...")
        regular_dst.write_text(
            json.dumps(self._regular, indent=4, ensure_ascii=False), encoding="utf-8")
        _log.info(f"Dumping {with_printings_count:,} decklist(s) to '{with_printings_dst}'...")
        with_printings_dst.write_text(
            json.dumps(self._with_printings, indent=4, ensure_ascii=False),encoding="utf-8")
        regular_delta = regular_count - self._initial_regular_count
        with_printings_delta = with_printings_count - self._initial_with_printings_count
        _log.info(
            f"Total of {abs(regular_delta):,} unique regular "
            f"decklist(s) {'subtracted from' if regular_delta < 0 else 'added to'} the global "
            f"repository")
        _log.info(
            f"Total of {abs(with_printings_delta):,} unique decklist(s) with printings"
            f" {'subtracted from' if with_printings_delta < 0 else 'added to'} the global "
            f"repository")

    def add_regular(self, decklist_hash: str, decklist: str) -> None:
        self._regular[decklist_hash] = decklist

    def add_with_printings(self, decklist_hash: str, decklist: str) -> None:
        self._with_printings[decklist_hash] = decklist

    def retrieve(self, decklist_hash: str) -> str | None:
        if not self._regular or not self._with_printings:
            _log.warning("No decklists. Are you sure you loaded them?")
        return self._regular.get(decklist_hash) or self._with_printings.get(decklist_hash)

    def prune(self, filter_: Callable[[str], bool]) -> None:
        """Prune all decklists that match the given filter.

        Args:
            filter_: a function that takes a decklist ID and returns a boolean.
        """
        self._regular = {k: v for k, v in self._regular.items() if not filter_(k)}
        self._with_printings = {k: v for k, v in self._with_printings.items() if not filter_(k)}


class CoolOffManager(_Singleton):
    """Keeps the things cool (with YT and pytube).
    """
    MAX_VIDEOS = 400

    @property
    def total_decks(self) -> int:
        return self._total_decks

    @property
    def total_videos(self) -> int:
        return self._total_videos

    @property
    def total_channels(self) -> int:
        return self._total_channels

    def reset(self) -> None:  # override
        self._total_decks, self._total_videos, self._total_channels = 0, 0, 0
        self._current_videos = 0

    def _cool_off(self) -> None:
        _log.info(f"Throttling for 5 minutes before the next batch...")
        throttle_with_countdown(5 * 60)
        self._current_videos = 0

    def bump_decks(self, decks: int) -> None:
        self._total_decks += decks

    def bump_channel(self) -> None:
        self._total_channels += 1

    def bump_video(self) -> None:
        self._total_videos += 1
        self._current_videos += 1
        if self._current_videos >= self.MAX_VIDEOS:
            self._cool_off()
