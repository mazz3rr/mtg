"""

    mtg.data.structs
    ~~~~~~~~~~~~~~~~
    Data structures for convenient consumption.

    @author: mazz3rr

"""
from dataclasses import asdict, astuple, dataclass, field, fields
from datetime import date, datetime
from functools import cached_property
from operator import attrgetter
from typing import Self

from mtg.constants import CHANNEL_URL_TEMPLATE, Json, VIDEO_URL_TEMPLATE
from mtg.deck.arena import ArenaParser
from mtg.deck.core import Deck
from mtg.lib.common import MarkdownTableCounter, breadcrumbs
from mtg.lib.json import to_json
from mtg.lib.scrape.core import extract_url, get_netloc_domain

ACTIVE_THRESHOLD = 14  # days (2 weeks)
DORMANT_THRESHOLD = 30 * 3  # days (ca 3 months)
ABANDONED_THRESHOLD = 30 * 12  # days (ca. 1 yr)
DECK_STALE_THRESHOLD = 50  # videos
VERY_DECK_STALE_THRESHOLD = 100  # videos
EXCESSIVELY_DECK_STALE_THRESHOLD = 150  # videos


@dataclass
class VideoData:
    yt_id: str
    title: str
    description: str
    keywords: list[str]
    publish_time: datetime
    views: int
    comment: str | None = None
    decks: list[Deck] = field(default_factory=list)
    # injected from Channel
    author: str | None = None  # same as channel's title
    scrape_time: datetime | None = None

    @property
    def url(self) -> str:
        return VIDEO_URL_TEMPLATE.format(self.yt_id)

    @property
    def deck_urls(self) -> set[str]:
        return {d.metadata["url"] for d in self.decks if d.metadata.get("url")}

    @property
    def featured_urls(self) -> list[str]:
        text = self.title + "\n" + self.description
        if self.comment:
            text += f"\n{self.comment}"
        lines = text.splitlines()
        return sorted({url for url in [extract_url(l) for l in lines] if url})

    @property
    def domains(self) -> list[str]:
        domains = [
            get_netloc_domain(url, naked=True).lower() for url in self.featured_urls
        ]
        return sorted({domain for domain in domains if domain})

    @property
    def as_dict(self) -> Json:
        data = asdict(self)
        if not self.comment:
            del data["comment"]
        data["decks"] = [d.as_dict for d in data["decks"]]
        del data["author"]
        del data["scrape_time"]
        return data

    @cached_property
    def json(self) -> str:
        return to_json(self.as_dict)

    @classmethod
    def from_dict(
            cls, data: Json, author: str | None = None,
            scrape_time: datetime | None = None) -> Self:
        field_names = {f.name for f in fields(cls)}
        data = {k: v for k, v in data.items() if k in field_names}
        data["decks"] = [ArenaParser(d["decklist"], d["metadata"]).parse() for d in data["decks"]]
        return cls(**data, author=author, scrape_time=scrape_time)


@dataclass
class ChannelData:
    yt_id: str
    title: str | None
    description: str | None
    tags: list[str] | None
    subscribers: int | None
    scrape_time: datetime
    videos: list[VideoData]

    @property
    def url(self) -> str:
        return CHANNEL_URL_TEMPLATE.format(self.yt_id)

    @property
    def decks(self) -> list[Deck]:
        return [d for v in self.videos for d in v.decks]

    @property
    def domains(self) -> list[str]:
        return sorted({domain for v in self.videos for domain in v.domains})

    @property
    def deck_urls(self) -> set[str]:
        return {url for v in self.videos for url in v.deck_urls}

    @property
    def deck_sources(self) -> MarkdownTableCounter:
        return MarkdownTableCounter(d.source for d in self.decks)

    @property
    def deck_formats(self) -> MarkdownTableCounter:
        return MarkdownTableCounter(
            d.metadata["format"] for d in self.decks if d.metadata.get("format"))

    @property
    def staleness(self) -> int | None:
        return (date.today() - self.videos[0].publish_time.date()).days if self.videos else None

    @property
    def deck_staleness(self) -> int:
        """Return number of last scraped videos without a deck.
        """
        if not self.decks:
            return len(self.videos)
        video_ids = [v.yt_id for v in self.videos]
        deck_video_ids = [v.yt_id for v in self.videos if v.decks]
        return len(self.videos[:video_ids.index(deck_video_ids[0])])

    @property
    def span(self) -> int | None:
        if not self.videos:
            return None
        return (self.videos[0].publish_time.date() - self.videos[-1].publish_time.date()).days

    @property
    def posting_interval(self) -> float | None:
        return self.span / len(self.videos) if self.videos else None

    @property
    def total_views(self) -> int:
        return sum(v.views for v in self.videos)

    @property
    def subs_activity(self) -> float | None:
        """Return ratio of subscribers needed to generate one video view in inverted relation to 10
        subscribers, if available.

        The greater this number the more active the subscribers. 1 means 10 subscribers are
        needed to generate one video view. 2 means 5 subscribers are needed to generate one
        video view, 10 means 1 subscriber is needed one and so on.
        """
        avg_views = self.total_views / len(self.videos)
        return 10 / (self.subscribers / avg_views) if self.subscribers else None

    @property
    def decks_per_video(self) -> float | None:
        if not self.videos:
            return None
        return len(self.decks) / len(self.videos)

    @property
    def is_abandoned(self) -> bool:
        return self.staleness is not None and self.staleness > ABANDONED_THRESHOLD

    @property
    def is_dormant(self) -> bool:
        return (self.staleness is not None
                and ABANDONED_THRESHOLD >= self.staleness > DORMANT_THRESHOLD)

    @property
    def is_active(self) -> bool:
        return (self.staleness is not None
                and DORMANT_THRESHOLD >= self.staleness > ACTIVE_THRESHOLD)

    @property
    def is_fresh(self) -> bool:
        return not self.is_abandoned and not self.is_dormant and not self.is_active

    @property
    def is_deck_stale(self) -> bool:
        return VERY_DECK_STALE_THRESHOLD >= self.deck_staleness > DECK_STALE_THRESHOLD

    @property
    def is_very_deck_stale(self) -> bool:
        return EXCESSIVELY_DECK_STALE_THRESHOLD >= self.deck_staleness > VERY_DECK_STALE_THRESHOLD

    @property
    def is_excessively_deck_stale(self) -> bool:
        return self.deck_staleness > EXCESSIVELY_DECK_STALE_THRESHOLD

    @property
    def is_deck_fresh(self) -> bool:
        return not (self.is_deck_stale or self.is_very_deck_stale or self.is_excessively_deck_stale)

    @cached_property
    def as_dict(self) -> Json:
        data = asdict(self)
        data["videos"] = [v.as_dict for v in self.videos]
        return data

    @property
    def json(self) -> str:
        return to_json(self.as_dict)

    @classmethod
    def from_dict(cls, data: Json, sort_videos_by_publish_time=True) -> Self:
        field_names = {f.name for f in fields(cls)}
        data = {k: v for k, v in data.items() if k in field_names}
        data["videos"] = [
            VideoData.from_dict(v, data["title"], data["scrape_time"])
            for v in data["videos"]
        ]
        if sort_videos_by_publish_time:
            data["videos"].sort(key=attrgetter("publish_time"), reverse=True)
        return cls(**data)


@dataclass(frozen=True)
class DataPath:
    """Structural path to a channel/video/decklist in the channel data.
    """
    channel_yt_id: str
    video_yt_id: str | None = None
    decklist_hash: str | None = None

    def __str__(self) -> str:
        return breadcrumbs(*[crumb for crumb in astuple(self) if crumb])

    @classmethod
    def from_path(cls, path: str) -> Self:
        parts = path.strip("/").split("/", maxsplit=2)
        return cls(*parts)
