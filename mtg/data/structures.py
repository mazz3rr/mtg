"""

    mtg.data.structures
    ~~~~~~~~~~~~~~~~~~~
    Data structures.

    @author: mazz3rr

"""
from dataclasses import asdict, astuple, dataclass, field, fields
from datetime import date, datetime
from functools import cached_property, lru_cache
from operator import attrgetter
from typing import Self

from mtg.constants import Json
from mtg.deck.arena import ArenaParser
from mtg.deck.core import Deck
from mtg.session import DecklistsStateManager
from mtg.lib.common import MarkdownTableCounter, breadcrumbs
from mtg.lib.json import to_json
from mtg.lib.scrape.core import extract_url, get_netloc_domain

VIDEO_URL_TEMPLATE = "https://www.youtube.com/watch?v={}"
CHANNEL_URL_TEMPLATE = "https://www.youtube.com/channel/{}"
ACTIVE_THRESHOLD = 14  # days (2 weeks)
DORMANT_THRESHOLD = 30 * 3  # days (ca 3 months)
ABANDONED_THRESHOLD = 30 * 12  # days (ca. 1 yr)
DECK_STALE_THRESHOLD = 50  # videos
VERY_DECK_STALE_THRESHOLD = 100  # videos
EXCESSIVELY_DECK_STALE_THRESHOLD = 150  # videos


@dataclass
class SerializedDeck:
    metadata: Json
    decklist_hash: str
    decklist_with_printings_hash: str

    def __hash__(self) -> int:
        return hash(self.json)

    def __eq__(self, other: Self) -> bool:
        return isinstance(other, type(self)) and self.json == other.json

    @cached_property
    def json(self) -> str:
        return to_json(asdict(self), sort_dictionaries=True)

    @property
    def source(self) -> str:
        return Deck.url_to_source(self.metadata.get("url"))

    @lru_cache
    def deck(self, with_printings=True) -> Deck | None:
        manager = DecklistsStateManager()
        if not manager.is_loaded:
            manager.load()
        decklist = manager.retrieve(
            self.decklist_with_printings_hash) if with_printings else manager.retrieve(
            self.decklist_hash)
        if not decklist:
            return None
        return ArenaParser(decklist, self.metadata).parse()


@dataclass
class Video:
    id: str
    author: str
    title: str
    description: str
    keywords: list[str]
    publish_time: datetime
    views: int
    comment: str | None = None
    decks: list[SerializedDeck] = field(default_factory=list)
    # injected from Channel
    scrape_time: datetime | None = None

    @property
    def url(self) -> str:
        return VIDEO_URL_TEMPLATE.format(self.id)

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
            get_netloc_domain(url).lower().removeprefix("www.") for url in self.featured_urls]
        return sorted({domain for domain in domains if domain})

    @cached_property
    def json(self) -> str:
        return to_json(self.as_dict)

    @property
    def as_dict(self) -> Json:
        data = asdict(self)
        del data["scrape_time"]
        if not self.comment:
            del data["comment"]
        return data

    @classmethod
    def from_dict(cls, data: Json, scrape_time: datetime | None = None) -> Self:
        field_names = {f.name for f in fields(cls)}
        data = {k: v for k, v in data.items() if k in field_names}
        data["decks"] = [SerializedDeck(**d) for d in data["decks"]]
        return cls(**data, scrape_time=scrape_time)


@dataclass
class Channel:
    id: str
    title: str | None
    description: str | None
    tags: list[str] | None
    subscribers: int | None
    scrape_time: datetime
    videos: list[Video]

    @property
    def url(self) -> str:
        return CHANNEL_URL_TEMPLATE.format(self.id)

    @property
    def decks(self) -> list[SerializedDeck]:
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
        return MarkdownTableCounter(d.metadata["format"] for d in self.decks if d.metadata.get("format"))

    @property
    def staleness(self) -> int | None:
        return (date.today() - self.videos[0].publish_time.date()).days if self.videos else None

    @property
    def deck_staleness(self) -> int:
        """Return number of last scraped videos without a deck.
        """
        if not self.decks:
            return len(self.videos)
        video_ids = [v.id for v in self.videos]
        deck_video_ids = [v.id for v in self.videos if v.decks]
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
    def json(self) -> str:
        return to_json(self.as_dict)

    @property
    def as_dict(self) -> Json:
        data = asdict(self)
        data["videos"] = [v.as_dict for v in self.videos]
        return data

    @classmethod
    def from_dict(cls, data: Json, sort_videos_by_publish_time=True) -> Self:
        field_names = {f.name for f in fields(cls)}
        data = {k: v for k, v in data.items() if k in field_names}
        data["videos"] = [Video.from_dict(v, data["scrape_time"]) for v in data["videos"]]
        if sort_videos_by_publish_time:
            data["videos"].sort(key=attrgetter("publish_time"), reverse=True)
        return cls(**data)


# TODO: not needed with db
@dataclass(frozen=True)
class DataPath:
    """Structural path to a channel/video/decklist in the channel data.
    """
    channel_id: str
    video_id: str | None = None
    decklist_hash: str | None = None

    def __str__(self) -> str:
        return breadcrumbs(*[crumb for crumb in astuple(self) if crumb])

    @classmethod
    def from_path(cls, path: str) -> Self:
        parts = path.strip("/").split("/", maxsplit=2)
        return cls(*parts)
