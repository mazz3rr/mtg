"""

    mtg.data.load
    ~~~~~~~~~~~~~
    Load scraped data from JSON to the database.

    @author: mazz3rr

"""
import itertools
import json
import logging
from operator import itemgetter
from pathlib import Path

from sqlalchemy import select
from tqdm import tqdm

from mtg.constants import CHANNELS_DIR, DECKLISTS_FILE, FAILED_URLS_FILE, WITHDRAWN_DIR
from mtg.data.db import DefaultSession, NoAutoFlushSession
from mtg.data.models import Channel, Deck, Decklist, FailedUrl, Snapshot, Tag, Video
from mtg.data.structures import DataPath
from mtg.lib.text import get_hash
from mtg.lib.time import timed
from mtg.lib.json import from_json

_log = logging.getLogger(__name__)


class MissedDecklist(RuntimeError):
    """Raised on encountering a missed decklist during a loading process.

    Using this simplifies logic of skipping a video containing a missed decklist from loading.
    """
    def __init__(self, *args, datapath: DataPath) -> None:
        super().__init__(*args)
        self.datapath = datapath


class Loader:
    """Load scraped data from JSON to the database.

    This loader assumes JSON data to load is already present in pre-defined destinations.

    1. Regular channels data is assumed to be present inside CHANNELS_DIR within
        sub-folders named with a channel YT ID - each keeping a channel's snapshot JSON file of
        format as seen in `samples` directory.
    2. Decklists and failed URLs data is assumed to also live inside CHANNELS_DIR in their
        dedicated JSONs (DECKLISTS_FILE and FAILED_URLS_FILE).
    3. Withdrawn channels data is assumed to be present inside WITHDRAWN_DIR within
        sub-folders named with a channel YT ID. Contents of those folders is irrelevant as only
        the IDs are saved to db.
    """
    def __init__(self) -> None:
        self._channels_jsons = self._load_channels(CHANNELS_DIR)
        self._withdrawn_jsons = {
            k: v for k, v in self._load_channels(WITHDRAWN_DIR).items()
            if k not in self._channels_jsons}
        self._decklists_json = json.loads(Path(DECKLISTS_FILE).read_text(encoding="utf-8"))
        self._failed_urls_json = json.loads(Path(FAILED_URLS_FILE).read_text(encoding="utf-8"))

    @staticmethod
    def _load_channels(channels_dir: str) -> dict[str, list]:
        sub_dirs = [d for d in Path(channels_dir).iterdir() if d.is_dir()]
        total = len(sub_dirs)
        data = {}
        for sub_dir in tqdm(sub_dirs, total=total, desc="Loading channels data..."):
            data[sub_dir.name] = [
            from_json(f.read_text(encoding="utf-8")) for f in Path(sub_dir).rglob("*.json")]
        return data

    def _populate_channels(self) -> None:
        _log.info("Populating channels...")
        with DefaultSession.begin() as session:
            for chid in self._channels_jsons:
                session.add(Channel(yt_id=chid))
            session.flush()
            for chid in self._withdrawn_jsons:
                session.add(Channel(yt_id=chid, is_withdrawn=True))

    def _populate_failed_urls(self) -> None:
        _log.info("Populating failed URLs...")
        with DefaultSession.begin() as session:
            for chid, urls in self._failed_urls_json.items():
                stmt = select(Channel).where(Channel.yt_id == chid)
                if channel := session.scalars(stmt).first():
                    for url in urls:
                        failed_url = FailedUrl(text=url)
                        channel.failed_urls.append(failed_url)
                        session.add(failed_url)

    def _populate_tags(self) -> None:
        _log.info("Populating tags...")
        tags = set()
        for snapshot in itertools.chain(*self._channels_jsons.values()):
            for tag in (snapshot["tags"] or []):
                if tag:
                    tags.add(tag)
            for video in snapshot["videos"]:
                for kw in (video["keywords"] or []):
                    if kw:
                        tags.add(kw)
        with DefaultSession.begin() as session:
            for tag in tags:
                session.add(Tag(text=tag))

    def _match_decklist_to_deck_data(
            self, deck_data: dict, video_data: dict, chid: str) -> tuple[str, dict | None]:
        if decklist_text := self._decklists_json.get(deck_data["decklist_hash"]):
            return decklist_text, deck_data["metadata"] or None
        raise MissedDecklist(datapath=DataPath(
            channel_id=chid,
            video_id=video_data["id"],
            decklist_hash=deck_data["decklist_hash"],
        ))

    def _populate_snapshots(self) -> None:
        missed_decklist_paths: list[DataPath] = []
        video_yt_ids, decklist_map = set(), {}
        with NoAutoFlushSession.begin() as session:
            for chid, snapshots in tqdm(
                    self._channels_jsons.items(), total=len(self._channels_jsons),
                    desc="Populating channel snapshots..."):
                stmt = select(Channel).where(Channel.yt_id == chid)
                channel = session.scalars(stmt).one()

                # snapshots
                # newest first, as we want to de-duplicate videos
                # (so, the newest stay and the older go)
                snapshots.sort(key=itemgetter("scrape_time"), reverse=True)
                for snapshot_data in snapshots:
                    snapshot = Snapshot(
                        title=snapshot_data["title"],
                        description=snapshot_data["description"],
                        subscribers=snapshot_data["subscribers"],
                        scrape_time=(snapshot_data["scrape_time"]),
                    )
                    # tags
                    if tags_data := snapshot_data.get("tags", []):
                        stmt = select(Tag).where(Tag.text.in_(tags_data))
                        for tag in session.scalars(stmt).all():
                            snapshot.tags.append(tag)

                    session.add(snapshot)
                    channel.snapshots.append(snapshot)

                    # videos
                    videos = sorted(snapshot_data["videos"], key=itemgetter("publish_time"))
                    for video_data in videos:
                        # de-duplicate videos
                        if video_data["id"] in video_yt_ids:
                            continue
                        else:
                            video_yt_ids.add(video_data["id"])

                        try:
                            video = Video(
                                yt_id=video_data["id"],
                                title=video_data["title"],
                                description=video_data["description"],
                                publish_time=video_data["publish_time"],
                                views=video_data["views"],
                                comment=video_data.get("comment"),
                            )
                            # keywords
                            if kw_data := video_data.get("keywords", []):
                                stmt = select(Tag).where(Tag.text.in_(kw_data))
                                for kw in session.scalars(stmt).all():
                                    video.keywords.append(kw)
                            # decks
                            pending: list[tuple[str, dict | None]] = []
                            for deck_data in video_data["decks"]:
                                pending.append(self._match_decklist_to_deck_data(
                                    deck_data, video_data, chid))

                            # updating db only after we're sure there was no MissedDecklist error
                            session.add(video)
                            snapshot.videos.append(video)

                            for decklist_text, json_metadata in pending:
                                # de-duplicate decklists
                                sha = get_hash(decklist_text, 40, sep="-")
                                decklist = decklist_map.get(sha)
                                if decklist is None:
                                    decklist = Decklist(
                                        hash=get_hash(decklist_text, 40, sep="-"),
                                        text=decklist_text
                                    )
                                    decklist_map[sha] = decklist

                                session.add(decklist)
                                deck = Deck(json_metadata=json_metadata)
                                session.add(deck)
                                decklist.decks.append(deck)
                                video.decks.append(deck)

                        except MissedDecklist as e:
                            # skip current video
                            missed_decklist_paths.append(e.datapath)

                session.flush()  # per channel

        if missed_decklist_paths:
            _log.warning(f"{len(missed_decklist_paths)} couldn't be resolved. Video data containing "
                         f"them was skipped from loading: {missed_decklist_paths}.")

    @timed("loading scraped data to database")
    def load(self) -> None:
        """Populate database with the loaded data.
        """
        self._populate_channels()
        self._populate_failed_urls()
        self._populate_tags()
        self._populate_snapshots()


if __name__ == '__main__':
    Loader().load()
