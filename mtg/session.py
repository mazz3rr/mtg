"""

    mtg.session
    ~~~~~~~~~~~
    State managers for scraping session's contexts.

    @author: mazz3rr

"""
import logging
import time
from datetime import datetime
from operator import attrgetter
from timeit import default_timer as timer
from types import TracebackType
from typing import Self, Type

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from mtg.data.db import NoAutoFlushSession, retrieve_or_create
from mtg.data.models import Channel, Deck, Decklist, FailedUrl, Snapshot, Tag, Video
from mtg.lib.common import from_iterable
from mtg.lib.scrape.core import normalize_url
from mtg.lib.text import get_hash
from mtg.lib.time import get_formatted_time

_log = logging.getLogger(__name__)


class CoolOffManager:
    """Keeps the things cool (with YT and pytube).
    """
    MAX_VIDEOS = 400

    def __init__(self) -> None:
        self.total_decks, self.total_videos, self.total_channels = 0, 0, 0
        self.current_videos = 0

    @staticmethod
    def _sleep_with_countdown(delay_seconds: int) -> None:
        for i in range(delay_seconds, 0, -1):
            print(f"Waiting {i} seconds before next batch...", end="\r")
            time.sleep(1)
        print("Ready for next batch!")

    def _cool_off(self) -> None:
        _log.info(f"Sleeping for 5 minutes before the next batch...")
        self._sleep_with_countdown(5 * 60)
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
    def ignore_failed(self) -> bool:
        """Return True, if avoidance of already failed deck URLs should be switched off for this
        session.
        """
        return self.__ignore_failed

    @ignore_failed.setter
    def ignore_failed(self, value: bool) -> None:
        """Set to True, if avoidance of already failed deck URLs should be switched off for this
        session.
        """
        self.__ignore_failed = value

    @property
    def ignore_scraped(self) -> bool:
        """Return True, if avoidance of already scraped deck URLs / parsed decklists should be
        switched off for this session.
        """
        return self.__ignore_scraped

    @ignore_scraped.setter
    def ignore_scraped(self, value: bool) -> None:
        """Set to True, if avoidance of already scraped deck URLs / parsed decklists should be
        switched off for this session.
        """
        self.__ignore_scraped = value

    def __init__(self, ignore_scraped=False, ignore_failed=False) -> None:
        self.ignore_scraped, self.ignore_failed = ignore_scraped, ignore_failed
        self._db: Session | None = None
        self._cooloff: CoolOffManager | None = None
        self._current_channel: Channel | None = None
        self._current_snapshot: Snapshot | None = None
        self._current_video: Video | None = None

    def __enter__(self) -> Self:
        self._start = timer()
        # as we want DB session's lifetime to be the same as this session's, we need a
        # fine-grained control when states are persisted in the session's context,
        # this enables exactly that by enforcing manual flushing to the db transaction's buffer
        self._db = NoAutoFlushSession()
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
        try:
            elapsed = get_formatted_time(timer() - self._start)
            if exc_type is None:
                _log.info(f"Scraping session finished successfully in {elapsed}. Committing changes "
                          f"to the database...")
                self._db.commit()
            else:
                _log.warning(
                    f"Scraping aborted after {elapsed} due to {exc_type}. Rolling back the database "
                    f"changes...")
                self._db.rollback()
        finally:
            self._db.close()

        self._cooloff = None
        self._current_channel, self._current_snapshot, self._current_video = None, None, None
        self.ignore_scraped, self.ignore_failed = False, False

    def add_channel(self, yt_id: str) -> None:
        """Add channel designated by the passed YouTube ID to this session and set it as the
        current one.
        """
        channel = retrieve_or_create(self._db, Channel, yt_id=yt_id)
        self._db.flush()
        self._current_channel = channel

    def get_video_yt_ids_for_current_channel(self) -> list[str]:
        """Return YT IDs of videos for the current channel of this session.
        """
        videos = sorted(
            [video for snapshot in self._current_channel.snapshots for video in snapshot.videos],
            key=attrgetter("publish_time"),
            reverse=True
        )
        return [video.yt_id for video in videos]

    def add_snapshot(
            self, title: str, description: str, tags: list[str], subscribers: int,
            scrape_time: datetime) -> None:
        """Add channel snapshot designated by the passed arguments to this session and set it as
        the current one.
        """
        snapshot = Snapshot(
            title=title,
            description=description,
            subscribers=subscribers,
            scrape_time=scrape_time,
        )
        self._db.add(snapshot)

        # tags
        if tags:
            existing_tags = set(self._db.scalars(select(Tag.text)).all())
            for tag_data in tags:
                if tag_data not in existing_tags:
                    tag = Tag(text=tag_data)
                    self._db.add(tag)
                    snapshot.tags.append(tag)

        self._current_channel.snapshots.append(snapshot)
        self._current_snapshot = snapshot
        self._db.flush()

    def add_video(
            self, yt_id: str, title: str, descritpion: str, keywords: list[str],
            publish_time: datetime, views: int) -> None:
        """Add video designated by the passed arguments to this session and set it as the current
        one.
        """
        video = Video(
            yt_id=yt_id,
            title=title,
            description=descritpion,
            publish_time=publish_time,
            views=views,
        )
        self._db.add(video)

        # keywords
        if keywords:
            existing_tags = set(self._db.scalars(select(Tag.text)).all())
            for kw_data in keywords:
                if kw_data not in existing_tags:
                    tag = Tag(text=kw_data)
                    self._db.add(tag)
                    video.keywords.append(tag)

        self._current_snapshot.videos.append(video)
        self._current_video = video
        self._db.flush()

    def update_video_comment(self, comment: str) -> None:
        """Update the current video's comment attribute.
        """
        self._current_video.comment = comment
        self._db.flush()

    def _remove_scraped_url_from_failed(self, json_metadata: dict):
        if url := json_metadata.get("url"):
            url = normalize_url(url)
            if failed_url := from_iterable(
                    self._current_channel.failed_urls, lambda furl: furl.text == url):
                self._current_channel.failed_urls.remove(failed_url)

    def add_deck(self, decklist_text: str, json_metadata: dict | None) -> None:
        """Add deck designated by the passed arguments to this session. Manage the deck's
        decklist and URL in the database.
        """
        sha = get_hash(decklist_text, 40, sep="-")
        decklist = retrieve_or_create(self._db, Decklist, hash=sha)
        decklist.text = decklist_text

        self._db.add(decklist)
        deck = Deck(json_metadata=json_metadata)
        self._db.add(deck)
        decklist.decks.append(deck)
        self._current_video.decks.append(deck)

        if json_metadata:
            self._remove_scraped_url_from_failed(json_metadata)

        self._db.flush()


    def is_scraped_url(self, url: str) -> bool:
        """Return True, if passed URL is among already scraped deck URLs for the current channel.
        """
        if self.ignore_scraped:
            return False
        for snapshot in self._current_channel.snapshots:
            for video in snapshot.videos:
                for deck in video.decks:
                    if deck.json_metadata and (deck_url := deck.json_metadata.get("url")):
                        if normalize_url(url) == normalize_url(deck_url):
                            return True
        return False


    def is_parsed_decklist(self, decklist: str) -> bool:
        """Return True, if passed decklist is among already parsed decklists for the current 
        channel.
        """
        if self.ignore_scraped:
            return False

        sha = get_hash(decklist, 40, sep="-")
        for snapshot in self._current_channel.snapshots:
            for video in snapshot.videos:
                for deck in video.decks:
                    if deck.decklist.hash == sha:
                        return True
        return False

    def is_failed_url(self, url: str) -> bool:
        """Return True, if passed URL is among already failed URLs for the current channel.
        """
        if self.ignore_failed:
            return False
        return normalize_url(url) in {
            furl.text for furl in self._current_channel.failed_urls}

    def add_failed_url(self, url: str) -> None:
        """Add passed URL as failed URL for the current channel.
        """
        url = normalize_url(url)
        if url in self._current_channel.failed_urls:
            return
        failed_url = FailedUrl(text=url)
        self._db.add(failed_url)
        self._current_channel.failed_urls.append(failed_url)
        self._db.flush()

    def prune_failed_urls(self, keeper_chids: set[str]) -> None:
        """Prune failed URLs from channels that don't have their YT IDs in 'keeper_chids'.
        """
        channels_count, urls_count = 0, 0
        stmt = select(Channel).where(Channel.yt_id.not_in(keeper_chids))

        for channel in self._db.scalars(stmt):
            failed_count = len(channel.failed_urls)
            if failed_count:
                channels_count += 1
                urls_count += failed_count
                channel.failed_urls.clear()

        self._db.flush()

        _log.info(
            f"Removed failed URLs data for {channels_count:,} channel(s) ({urls_count:,} URL(s) "
            f"in total)")

    def remove_videos(self, video_ids: set[str]) -> None:
        """Remove videos from the database by passed YouTube IDs.
        """
        stmt = delete(Video).where(Video.yt_id.in_(video_ids))
        self._db.execute(stmt)
        self._db.flush()

    # CoolOffManager API
    def bump_channel(self) -> None:
        self._cooloff.bump_channel()

    def bump_video(self) -> None:
        self._cooloff.bump_video()

    def bump_decks(self, decks: int) -> None:
        self._cooloff.bump_decks(decks)
