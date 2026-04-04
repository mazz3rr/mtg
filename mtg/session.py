"""

    mtg.session
    ~~~~~~~~~~~
    State managers for scraping session's contexts.

    @author: mazz3rr

"""
import logging
from types import TracebackType
from typing import Self, Type

from sqlalchemy import select
from sqlalchemy.orm import Session

from mtg.data.db import NoAutoFlushSession
from mtg.data.models import Channel, FailedUrl, Snapshot, Video
from mtg.lib.scrape.core import throttle_with_countdown

_log = logging.getLogger(__name__)


def normalize_url(url: str) -> str:
    return url.removesuffix("/").lower()


# TODO: each successfully scraped URL should be removed from failed ones (important for
#  re-scraping)
class UrlsStateManager:
    """State manager for already scraped and failed URLs.
    """
    def __init__(self, db_session: Session) -> None:
        self._db_session = db_session
        self.current_snapshot, self.current_video = None, None
        self.ignore_scraped, self.ignor_failed = False, False

    def is_scraped(self, url: str) -> bool:
        if self.ignore_scraped:
            return False
        scraped_urls = set()
        for video in self.current_snapshot.channel.videos:
            for deck in video.decks:
                if deck.json_metadata and (deck_url := deck.json_metadata.get("url")):
                    scraped_urls.add(deck_url)
        return normalize_url(url) in scraped_urls

    def is_failed(self, url: str) -> bool:
        if self.ignore_failed or not self.current_snapshot:
            return False
        return normalize_url(url) in {
            furl.text for furl in self.current_snapshot.channel.failed_urls}

    def add_failed(self, url: str) -> None:
        if not self.current_snapshot:
            return
        failed_url = FailedUrl(text=normalize_url(url))
        self.current_snapshot.channel.failed_urls.append(failed_url)
        self._db_session.flush()

    def prune_failed(self, keeper_chids: set[str]) -> None:
        """Prune failed URLs from channels that are not in 'keeper_chids'.
        """
        channels_count, urls_count = 0, 0
        stmt = select(Channel).where(Channel.yt_id.not_in(keeper_chids))

        for channel in self._db_session.scalars(stmt):
            failed_count = len(channel.failed_urls)
            if failed_count:
                channels_count += 1
                urls_count += failed_count
                channel.failed_urls.clear()

        self._db_session.flush()

        _log.info(
            f"Removed failed URLs data for {channels_count:,} channel(s) ({urls_count:,} URL(s) "
            f"in total)")


class CoolOffManager:
    """Keeps the things cool (with YT and pytube).
    """
    MAX_VIDEOS = 400

    def __init__(self) -> None:
        self.total_decks, self.total_videos, self.total_channels = 0, 0, 0
        self.current_videos = 0

    def _cool_off(self) -> None:
        _log.info(f"Throttling for 5 minutes before the next batch...")
        throttle_with_countdown(5 * 60)
        self.current_videos = 0

    def bump_channel(self) -> None:
        self.total_channels += 1

    def bump_video(self) -> None:
        self.total_videos += 1
        self.current_videos += 1
        if self.current_videos >= self.MAX_VIDEOS:
            self._cool_off()

    def bump_decks(self, decks: int) -> None:
        self.total_decks += decks


class ScrapingSession:
    """Context manager to ensure proper state management during scraping.
    """
    @property
    def db_session(self) -> Session | None:
        return self._db_session

    @property
    def current_snapshot(self) -> Snapshot | None:
        return self._usm.current_snapshot

    @current_snapshot.setter
    def current_snapshot(self, value: Snapshot | None) -> None:
        self._usm.current_snapshot = value

    @property
    def current_video(self) -> Video | None:
        return self._usm.current_video

    @current_video.setter
    def current_video(self, value: Video | None) -> None:
        self._usm.current_video = value

    @property
    def ignore_failed(self) -> bool:
        return self._usm.ignore_failed

    @ignore_failed.setter
    def ignore_failed(self, value: bool) -> None:
        self._usm.ignore_failed = value

    @property
    def ignore_scraped(self) -> bool:
        return self._usm.ignore_scraped

    @ignore_scraped.setter
    def ignore_scraped(self, value: bool) -> None:
        self._usm.ignore_scraped = value

    def __init__(self) -> None:
        self._db_session: Session | None = None
        self._usm: UrlsStateManager | None = None
        self._cooloff: CoolOffManager | None = None

    def __enter__(self) -> Self:
        # as we want DB session's lifetime to be the same as this session's, we need a
        # fine-grained control when states are persisted in the session's context,
        # this enables exactly that by enforcing manual flushing to the db transaction's buffer
        self._db_session = NoAutoFlushSession()
        self._usm = UrlsStateManager(self.db_session)
        self._cooloff = CoolOffManager()
        return self

    def __exit__(
            self, exc_type: Type[BaseException] | None, exc_val: BaseException | None,
            exc_tb: TracebackType | None) -> None:
        _log.info(
            f"Session finished with: {self._cooloff.total_decks} deck(s) from "
            f"{self._cooloff.total_videos} video(s) from "
            f"{self._cooloff.total_channels} channel(s) scraped in total")

        # db session finalization
        if exc_type is None:
            self._db_session.commit()
        else:
            self._db_session.rollback()
        self._db_session.close()

        self._usm = None
        self._cooloff = None

    # UrlsStateManager API
    def is_scraped(self, url: str) -> bool:
        return self._usm.is_scraped(url)

    def is_failed(self, url: str) -> bool:
        return self._usm.is_failed(url)

    def add_failed(self, url: str) -> None:
        self._usm.add_failed(url)

    def prune_failed(self, keeper_chids: set[str]) -> None:
        """Prune failed URLs from channels that are not in 'keeper_chids'.
        """
        self._usm.prune_failed(keeper_chids)

    # CoolOffManager API
    def bump_channel(self) -> None:
        self._cooloff.bump_channel()

    def bump_video(self) -> None:
        self._cooloff.bump_video()

    def bump_decks(self, decks: int) -> None:
        self._cooloff.bump_decks(decks)
