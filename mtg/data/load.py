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
from sqlalchemy.orm import Session
from tqdm import tqdm

from mtg.constants import WITHDRAWN_DIR
from mtg.data.db import ENGINE
from mtg.data.models import Channel, Deck, Decklist, FailedUrl, Snapshot, Tag, Video
from mtg.data.structures import DataPath
from mtg.gstate import CHANNELS_DIR, DECKLISTS_FILE, FAILED_URLS_FILE
from mtg.lib.common import get_hash, timed
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
        with Session(ENGINE) as session:
            for chid in self._channels_jsons:
                session.add(Channel(yt_id=chid))
            session.flush()
            for chid in self._withdrawn_jsons:
                session.add(Channel(yt_id=chid, is_withdrawn=True))
            session.commit()

    def _populate_failed_urls(self) -> None:
        _log.info("Populating failed URLs...")
        with Session(ENGINE) as session:
            for chid, urls in self._failed_urls_json.items():
                stmt = select(Channel).where(Channel.yt_id == chid)
                if channel := session.scalars(stmt).first():
                    for url in urls:
                        failed_url = FailedUrl(text=url)
                        channel.failed_urls.append(failed_url)
                        session.add(failed_url)
            session.commit()

    def _populate_tags(self) -> None:
        _log.info("Populating tags...")
        tags = set()
        for snapshot in itertools.chain(*self._channels_jsons.values()):
            for tag in (snapshot["tags"] or []):
                tags.add(tag)
            for video in snapshot["videos"]:
                for kw in (video["keywords"] or []):
                    tags.add(kw)
        with Session(ENGINE) as session:
            for tag in tags:
                session.add(Tag(text=tag))
        session.commit()

    def _process_deck_in_video(
            self, deck_data: dict, video_data: dict, chid: str) -> tuple[Deck, Decklist]:
        if decklist_text := self._decklists_json.get(deck_data["decklist_hash"]):
            decklist = Decklist(
                hash=get_hash(decklist_text, 40, sep="-"),
                text=decklist_text
            )
            deck = Deck(json_metadata=deck_data["metadata"] or None)
            return deck, decklist
        raise MissedDecklist(datapath=DataPath(
            channel_id=chid,
            video_id=video_data["id"],
            decklist_hash=deck_data["decklist_hash"],
        ))

    def _populate_snapshots(self) -> None:
        missed_decklists: list[DataPath] = []
        with Session(ENGINE) as session:
            for chid, snapshots in tqdm(
                    self._channels_jsons.items(), total=len(self._channels_jsons),
                    desc="Populating channel snapshots..."):
                stmt = select(Channel).where(Channel.yt_id == chid)
                channel = session.scalars(stmt).one()

                # snapshots
                snapshots.sort(key=itemgetter("scrape_time"))
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

                    # videos
                    videos = sorted(snapshot_data["videos"], key=itemgetter("publish_time"))
                    for video_data in videos:
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
                            pending_decks: list[tuple[Deck, Decklist]] = []
                            for deck_data in video_data["decks"]:
                                pending_decks.append(self._process_deck_in_video(
                                    deck_data, video_data, chid))
                            # updating db only after we're sure there was no MissedDecklist error
                            for deck, decklist in pending_decks:
                                decklist.decks.append(deck)
                                session.add(decklist)
                                video.decks.append(deck)
                                session.add(deck)
                            snapshot.videos.append(video)
                            session.merge(video)  # duplicates (if any) get overwritten
                        except MissedDecklist as e:
                            # skip current video
                            missed_decklists.append(e.datapath)

                    channel.snapshots.append(snapshot)
                    session.add(snapshot)

                session.flush()  # per channel

            session.commit()

        if missed_decklists:
            _log.warning(f"{len(missed_decklists)} couldn't be resolved. Video data containing "
                         f"them was skipped from loading: {missed_decklists}.")

    @timed("Loading scraped data to database")
    def load(self) -> None:
        """Populate database with the loaded data.
        """
        self._populate_channels()
        self._populate_failed_urls()
        self._populate_tags()
        self._populate_snapshots()


if __name__ == '__main__':
    Loader().load()
