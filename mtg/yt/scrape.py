"""

    mtg.yt.scrape
    ~~~~~~~~~~~~~
    Scrape YouTube videos and channels.

    @author: mazz3rr

"""
import json
import logging
import traceback
import urllib.error
from dataclasses import astuple
from functools import cached_property
from http.client import RemoteDisconnected
from typing import Iterator

import backoff
import pytubefix
import pytubefix.exceptions
import scrapetube
from requests import ConnectionError, HTTPError, ReadTimeout, Timeout
from tqdm import tqdm
from youtube_comment_downloader import SORT_BY_POPULAR, YoutubeCommentDownloader

from mtg import DeckScraper, DeckUrlsContainerScraper, HybridContainerScraper
from mtg.constants import (
    CHANNELS_DIR, CHANNEL_URL_TEMPLATE, FILENAME_TIMESTAMP_FORMAT, Json,
    PathLike, VIDEO_URL_TEMPLATE,
)
from mtg.data.common import load_channel, retrieve_ids
from mtg.data.structs import ChannelData, VideoData
from mtg.deck.arena import ArenaParser, LinesParser
from mtg.deck.core import Deck, DeckParser
from mtg.deck.scrapers.abc import (
    DeckTagsContainerScraper, DecksJsonContainerScraper,
    get_video_throttled_deck_scraper_types,
)
from mtg.lib.common import Noop, find_longest_seqs, from_iterable, logging_disabled
from mtg.lib.files import get_dir, sanitize_filename
from mtg.lib.numbers import extract_float, multiply_by_symbol
from mtg.lib.scrape.core import (
    ScrapingError, extract_url, http_requests_counted,
    parse_keywords, throttle, throttled, unshorten,
)
from mtg.lib.scrape.dynamic import fetch_dynamic_soup
from mtg.lib.scrape.linktree import LinktreeScraper
from mtg.lib.time import naive_utc_now, timed
from mtg.scryfall import all_formats
from mtg.session import ScrapingSession
from mtg.yt.expand import LinksExpander
from mtg.yt.ptfix import PytubeChannelWrapper, PytubeVideoWrapper

_log = logging.getLogger(__name__)
DEAD_THRESHOLD = 2000  # days (ca. 5.5 yrs) - only used in gsheet to trim dead from abandoned
VIDEOS_COUNT = 100  # default max number of videos to scrape on regular basis


class MissingVideoPublishTime(ScrapingError):
    """Raised on pytubefix failing to retrieve video publish time.
    """


class VideoScraper:
    """Scrape YouTube video for MtG decks data.
    """
    SHORTENER_HOOKS = {
        "73.nu/",
        "bit.ly/",
        "bitly.kr/",
        "bl.ink/",
        "bli.nk/",
        "buff.ly/",
        "clicky.me/",
        "cmkt.co/",  # Cardmarket link
        "cutt.ly/",
        "dub.co/",
        "dub.sh/",
        "fox.ly/",
        "gg.gg/",
        "han.gl/",
        "is.gd/",
        "kurzelinks.de/",
        "kutt.it/",
        "lstu.fr/",
        "lyksoomu.com/",
        "lyn.bz/",
        "name.com/",
        "oe.cd/",
        "ow.ly/",
        "partner.tcgplayer.com/",  # TCGPlayer affiliate referral link
        "qti.ai/",
        "rb.gy/",
        "rebrandly.com/",
        "reduced.to/",
        "rip.to/",
        "san.aq/",
        "short.io/",
        "shortcm.li/",
        "shorten-url.com/",
        "shorturl.at/",
        "snip.ly/",
        "sor.bz/",
        "spoo.me/",
        "switchy.io/",
        "t.ly/",
        "pxf.io",  # e.g. tcgplayer.pxf.io affiliate referral link
        "tinu.be/",
        "tiny.cc/",
        "tinyurl.com/",
        "urlr.me/",
        "urlzs.com/",
        "v.gd/",
        "vo.la/",
        "yaso.su/",
        "zlnk.com/",
        "zws.im/",
    }

    @property
    def url(self) -> str:
        return VIDEO_URL_TEMPLATE.format(self._yt_id)

    @cached_property
    def _desc_lines(self) -> list[str]:
        return [
            line.strip() for line in self._description.splitlines()] if self._description else []

    @property
    def deck_metadata(self) -> Json:
        metadata = {}
        if self._derived_deck_format:
            metadata["format"] = self._derived_deck_format
        if self._derived_deck_name:
            metadata["name"] = self._derived_deck_name
        if self._author:
            metadata["author"] = self._author
        if self._publish_time:
            metadata["date"] = self._publish_time.date()
        return metadata

    @property
    def data(self) -> VideoData | None:
        return self._data

    @property
    def channel_id(self) -> str | None:
        return self._channel_id

    def __init__(self, video_id: str, session: ScrapingSession | Noop | None) -> None:
        """Initialize.

        Args:
            video_id: unique string identifying a YouTube video (the part after `v=` in the URL)
        """
        self._yt_id = video_id
        self._session = session or Noop()
        # description and title is also available in scrapetube data on Channel abstraction layer
        self._author, self._description, self._title = None, None, None
        self._keywords, self._publish_time, self._views = None, None, None
        self._comment, self._channel_id = None, None
        self._pytube, self._data = None, None
        self._derived_deck_format, self._derived_deck_name = None, None

    @backoff.on_exception(
        backoff.expo,
        (Timeout, HTTPError, RemoteDisconnected, MissingVideoPublishTime, urllib.error.HTTPError),
        max_time=300)
    def _get_pytube(self) -> PytubeVideoWrapper:
        try:
            pytube = pytubefix.YouTube(self.url, use_oauth=True, allow_oauth_cache=True)
        except pytubefix.exceptions.RegexMatchError as rme:
            raise ValueError(f"Invalid video ID: {self._yt_id!r}") from rme
        if not pytube.publish_date:
            raise MissingVideoPublishTime(
                "pytubefix data missing publish time", scraper=type(self), url=self.url)
        wrapper = PytubeVideoWrapper(pytube)
        wrapper.retrieve()
        return wrapper

    def _save_pytube_data(self) -> None:
        self._author = self._pytube.data.author
        self._description = self._pytube.data.description
        self._title = self._pytube.data.title
        self._keywords = self._pytube.data.keywords
        self._publish_time = self._pytube.publish_time
        self._views = self._pytube.data.views
        self._channel_id = self._pytube.channel_id

    def _scrape_video(self) -> None:
        self._pytube = self._get_pytube()
        self._save_pytube_data()
        self._session.add_video(
            yt_id=self._yt_id,
            title=self._title,
            descritpion=self._description,
            keywords=self._keywords,
            publish_time=self._publish_time,
            views=self._views,
        )

    def _derive_deck_format(self) -> str | None:
        # first, check the keywords
        if self._keywords:
            if fmt := DeckParser.derive_format_from_words(*self._keywords, use_japanese=True):
                return fmt
        # then the title and description
        return DeckParser.derive_format_from_text(
            self._title + self._description, use_japanese=True)

    def _derive_deck_name(self) -> str | None:
        if not self._keywords:
            return None
        # identify title parts that are also parts of keywords
        kw_unwanted = {"mtg", "#mtg", "magic", "#magic"}
        kw_soup = {w.lower().lstrip("#") for kw in self._keywords for w in kw.strip().split()
                   if w.lower() not in kw_unwanted}
        indices = []
        title_unwanted = {"gameplay"}
        title_words = [w for w in self._title.strip().split() if w.lower() not in title_unwanted]
        for i, word in enumerate([tw.lower() for tw in title_words]):
            if word in kw_soup:
                indices.append(i)
        if len(indices) < 2:
            return None
        # look for the longest sequence of identified indices
        seqs = find_longest_seqs(indices)
        if len(seqs) > 1:
            if len(seqs[0]) < 2:
                return None
            seq = from_iterable(
                seqs, lambda s: " ".join(title_words[i] for i in s).lower() in kw_soup)
            if not seq:
                return None
        else:
            seq = seqs[0]
        # check final conditions
        if len(seq) < 2:
            return None
        if len(seq) == 2 and any(title_words[i].lower() in all_formats() for i in seq):
            return None
        return " ".join(title_words[i] for i in seq)

    @classmethod
    def is_shortened_url(cls, url: str) -> bool:
        return any(h in url for h in cls.SHORTENER_HOOKS)

    def _parse_lines(self, *lines, expand_links=True) -> tuple[list[str], list[str]]:
        links, other_lines = set(), []
        for line in lines:
            url = extract_url(line)
            if url:
                if self.is_shortened_url(url):
                    url = unshorten(url) or url
                if LinktreeScraper.is_linktree_url(url):
                    try:
                        new_links = LinktreeScraper(url).data.links
                        _log.info(f"Parsed {len(new_links)} link(s) from: {url!r}")
                        links.update(new_links)
                    except (ConnectionError, HTTPError, ReadTimeout, ScrapingError) as err:
                        _log.warning(f"Parsing '{url!r}' failed with: {err!r}")
                else:
                    links.add(url)
            else:
                other_lines.append(line)

        if expand_links:
            expander = LinksExpander(*links, session=self._session)
            links = {l for l in links if l not in expander.expanded_links}
            links.update(expander.gathered_links)
            new_links, new_lines = self._parse_lines(*expander.lines, expand_links=False)
            links.update(new_links)
            other_lines.extend(new_lines)

        return sorted(links), other_lines

    @timed("comments lookup")
    def _get_comment_lines(self) -> list[str]:
        downloader = YoutubeCommentDownloader()
        try:
            comments = downloader.get_comments_from_url(self.url, sort_by=SORT_BY_POPULAR)
        except (RuntimeError, json.JSONDecodeError):
            return []
        if not comments:
            return []
        author_comments = [c for c in comments if c["channel"] == self.channel_id]
        return [line for c in author_comments for line in c["text"].splitlines()]

    def _scrape_deck(self, link: str) -> Deck | None:
        deck = None
        if scraper := DeckScraper.from_url(link, self.deck_metadata, self._session):
            normalized_link = scraper.normalize_url(link)
            if self._session.is_scraped_url(normalized_link):
                _log.info(f"Skipping already scraped deck URL: {normalized_link!r}...")
                return None
            elif self._session.is_failed_url(normalized_link):
                _log.info(f"Skipping already failed deck URL: {normalized_link!r}...")
                return None
            try:
                deck = scraper.scrape(throttled=type(scraper) in get_video_throttled_deck_scraper_types())
            except ReadTimeout:
                _log.warning(f"Back-offed scraping of {link!r} failed with read timeout")
        return deck

    def _process_urls(self, *urls: str) -> list[Deck]:
        decks = []
        for url in urls:
            if deck := self._scrape_deck(url):
                decks.append(deck)
        return decks

    def _process_lines(self, *lines: str) -> list[Deck]:
        decks, lp = [], LinesParser(*lines)
        for decklist in lp.parse():
            if deck := ArenaParser(decklist, self.deck_metadata).parse():
                if self._session.is_parsed_decklist(deck.decklist):
                    _log.info(f"Skipping already parsed text decklist...")
                    continue
                deck_name = f"{deck.name!r} deck" if deck.name else "Deck"
                _log.info(f"{deck_name} scraped successfully")
                decks.append(deck)
        if not decks:
            if decklists := lp.parse(single_decklist_mode=True):
                if deck := ArenaParser(decklists[0], self.deck_metadata).parse():
                    if self._session.is_parsed_decklist(deck.decklist):
                        _log.info(f"Skipping already parsed text decklist...")
                    else:
                        deck_name = f"{deck.name!r} deck" if deck.name else "Deck"
                        _log.info(f"{deck_name} scraped successfully")
                        decks.append(deck)
        for deck in decks:
            self._session.add_deck(deck.decklist, deck.metadata or None)
        return decks

    def _collect(self, links: list[str], lines: list[str]) -> list[Deck]:
        decks: set[Deck] = set()

        # 1st stage: deck links
        decks.update(self._process_urls(*links))

        # 2nd stage: deck container links
        for link in links:
            # skipping of already scraped/failed links for JSON, tag and hybrid scrapers happens
            # here; the same skipping happens for deck URLs scrapers within them per each
            # individual deck URL; skipping for hybrid scrapers happens ONLY if their JSON or tag
            # parts flag the container URL as scraped/failed
            if scraper := DeckUrlsContainerScraper.from_url(
                    link, self.deck_metadata, self._session):
                decks.update(scraper.scrape_decks())
            elif scraper := DecksJsonContainerScraper.from_url(
                    link, self.deck_metadata, self._session) or DeckTagsContainerScraper.from_url(
                link, self.deck_metadata, self._session) or HybridContainerScraper.from_url(
                link, self.deck_metadata, self._session):
                normalized_link = scraper.normalize_url(link)
                if self._session.is_scraped_url(normalized_link):
                    _log.info(
                        f"Skipping already scraped {scraper.short_name()} URL: "
                        f"{normalized_link!r}...")
                    continue
                if self._session.is_failed_url(normalized_link):
                    _log.info(
                        f"Skipping already failed {scraper.short_name()} URL: "
                        f"{normalized_link!r}...")
                    continue
                decks.update(scraper.scrape_decks())

        # 3rd stage: text decklists
        decks.update(self._process_lines(*lines))

        return sorted(decks)

    def _scrape_decks(self) -> None:
        self._derived_deck_format = self._derive_deck_format()
        self._derived_deck_name = self._derive_deck_name()
        links, lines = self._parse_lines(self._title, *self._desc_lines)
        self._decks = self._collect(links, lines)
        if not self._decks:  # try with author's comment
            comment_lines = self._get_comment_lines()
            if comment_lines:
                links, lines = self._parse_lines(*comment_lines)
                self._decks = self._collect(links, lines)
                if self._decks:
                    self._comment = "\n".join(comment_lines)
                    self._session.update_video_comment(self._comment)
        self._session.bump_decks(len(self._decks))

    @timed("video scraping")
    @throttled(1.25, 0.25)
    def scrape(self) -> None:
        self._scrape_video()
        self._scrape_decks()
        # saving author in data is redundant as it's the same as the channel's title
        self._data = VideoData(
            self._yt_id,
            self._title,
            self._description,
            self._keywords,
            self._publish_time,
            self._views,
            self._comment,
            self._decks
        )


class MissingChannelData(ScrapingError):
    """Raised on Channel missing scrapeable data.
    """
    def __init__(self, message: str, channel: str, url: str) -> None:
        channel = channel or "Channel"
        details = [f"'{channel}'", url]
        message += f" [{', '.join(details)}]"
        super().__init__(message, None, None)


class ChannelScraper:
    """Scrape YouTube channel's videos for MtG deck data.
    """
    CONSENT_XPATH = "//button[@aria-label='Accept all']"
    XPATH = "//span[contains(., 'subscriber')]"

    @property
    def url(self) -> str:
        return CHANNEL_URL_TEMPLATE.format(self._yt_id)

    @property
    def data(self) -> ChannelData | None:
        return self._data

    @property
    def has_session(self) -> bool:
        return isinstance(self._session, ScrapingSession)

    def __init__(self, channel_id: str, session: ScrapingSession | None) -> None:
        self._yt_id = channel_id
        self._session = session or Noop()
        self._title, self._subscribers, self._description, self._tags = None, None, None, None
        self._scrape_time, self._videos = None, []
        self._data = None
        self._session.add_channel(self._yt_id)

    def video_ids(self, limit=10) -> Iterator[str]:
        try:
            for ch_data in scrapetube.get_channel(channel_id=self._yt_id, limit=limit):
                yield ch_data["videoId"]
        except OSError as ose:
            raise ValueError(f"Invalid channel ID: {self._yt_id!r}") from ose
        except json.decoder.JSONDecodeError as jde:
            raise ScrapingError(
                "scrapetube failed with JSON error. This channel probably doesn't exist "
                "anymore", scraper=type(self), url=self.url) from jde

    def _process_vids(
            self, limit: int, last_scraped_id: str | None,
            scraped_ids: list[str] | list[str]) -> tuple[int, list[str]]:
        video_ids, scraped_ids = [], set(scraped_ids)
        count = 0
        for vid in self.video_ids(limit=limit):
            count += 1
            if vid == last_scraped_id:
                break
            elif vid not in scraped_ids:
                video_ids.append(vid)
        return count, video_ids

    def _get_unscraped_video_ids(
            self, limit=10,
            only_newer_than_last_scraped=True) -> list[str]:
        scraped_ids = self._session.get_video_yt_ids_for_current_channel()
        if not scraped_ids:
            last_scraped_id = None
        else:
            last_scraped_id = scraped_ids[0] if only_newer_than_last_scraped else None

        count, video_ids = self._process_vids(limit, last_scraped_id, scraped_ids)
        if not count:
            _log.warning("scrapetube failed to yield any video IDs. Retrying...")
            throttle(0.3, 0.1)
            count, video_ids = self._process_vids(limit, last_scraped_id, scraped_ids)
            if not count:
                raise MissingChannelData(
                    "scrapetube failed to yield any video IDs even after a re-try. "
                    "Are you sure the channel has a 'Videos' tab?", self._title, url=self.url)

        return video_ids

    def get_unscraped_video_ids(
            self, limit=10,
            only_newer_than_last_scraped=True,
            soft_limit=False) -> list[str]:
        """Return a list of yet unscraped videos' YT IDs of this channel.

        The limit is strictly observed only if `soft_limit=False`.

        See scrape() for the more in-depth description of the parameters.
        """
        if not self.has_session:
            _log.warning(
                "You cannot reason about unscraped IDs without a session. If you used `scrape()`, "
                "use `scrape_videos()` passing video IDs explicitly instead")
            return []
        video_ids = self._get_unscraped_video_ids(limit, only_newer_than_last_scraped)
        if not soft_limit:
            return video_ids

        original_limit = limit
        while len(video_ids) == limit:
            limit += original_limit
            video_ids = self._get_unscraped_video_ids(limit, only_newer_than_last_scraped)

        return video_ids

    def url_title_text(self) -> str:
        return f"{self.url!r} ({self._title})" if self._title else f"{self.url!r}"

    # not currently used
    def _fetch_info_with_selenium(  # not used
            self) -> tuple[str | None, str | None, list[str] | None, int | None]:
        soup, _, _ = fetch_dynamic_soup(self.url, self.XPATH, consent_xpath=self.CONSENT_XPATH)
        title, description, tags, subscribers = None, None, None, None

        # title (from <meta> or <title>)
        title_tag = soup.find('meta', property='og:title') or soup.find('title')
        if title_tag:
            title = title_tag.get('content', title_tag.text.replace(
                ' - YouTube', '').strip())

        # description (from <meta name="description">)
        if description_tag := soup.find('meta', {'name': 'description'}):
            description = description_tag.get('content', None)

        # tags (from <meta name="keywords">)
        if keywords_tag := soup.find('meta', {'name': 'keywords'}):
            tags = parse_keywords(keywords_tag)

        # subscribers
        if count_text := soup.find(
            "span", string=lambda t: t and "subscriber" in t).text.removesuffix(
            " subscribers").removesuffix(" subscriber"):
            subscribers = extract_float(count_text)
            if count_text[-1] in {"K", "M", "B", "T"}:
                subscribers = multiply_by_symbol(subscribers, count_text[-1])
            subscribers = int(subscribers)

        return title, description, tags, subscribers

    @backoff.on_exception(
        backoff.expo,
        (Timeout, HTTPError, RemoteDisconnected, urllib.error.HTTPError),
        max_time=300)
    def _fetch_info_with_pytube(self) -> tuple[str, str, list[str], int]:
        pytube = pytubefix.Channel(self.url, use_oauth=True, allow_oauth_cache=True)
        wrapper = PytubeChannelWrapper(pytube)
        wrapper.retrieve()
        return astuple(wrapper.data)

    def _scrape_videos(self, *video_ids: str) -> None:
        text = self.url_title_text()
        _log.info(f"Scraping channel: {text}, {len(video_ids)} video(s)...")

        self._scrape_time = naive_utc_now()
        # self._title, self._description, self._tags, self._subscribers = self._fetch_info_with_selenium()
        self._title, self._description, self._tags, self._subscribers = self._fetch_info_with_pytube()
        self._session.add_snapshot(
            self._title, self._description, self._tags, self._subscribers, self._scrape_time)

        self._videos, scraper = [], None
        for i, vid in enumerate(video_ids, start=1):
            _log.info(
                f"Scraping video {i}/{len(video_ids)}: '{VIDEO_URL_TEMPLATE.format(vid)}'...")
            scraper = VideoScraper(vid, self._session)
            try:
                scraper.scrape()
            except pytubefix.exceptions.VideoPrivate:
                _log.warning(f"Skipping private video: '{VIDEO_URL_TEMPLATE.format(vid)}'...")
                continue
            except MissingVideoPublishTime:
                _log.warning(
                    f"Skipping video that pytubefix failed to retrieve a publish time for: "
                    f"'{VIDEO_URL_TEMPLATE.format(vid)}'...")
                continue
            if scraper.channel_id != self._yt_id:
                _log.warning(
                    f"Skipping video with a wrong channel ID: {scraper.channel_id!r} != "
                    f"{self._yt_id!r}")
                continue
            self._videos.append(scraper.data)
            self._session.bump_video()

        self._data = ChannelData(
            yt_id=self._yt_id,
            title=self._title,
            description=self._description,
            tags=sorted(set(self._tags)) if self._tags else None,
            subscribers=self._subscribers,
            scrape_time=self._scrape_time,
            videos=self._videos,
        )
        text = self.url_title_text()
        sources = sorted({d.source for d in self.data.decks if d.source})
        if sources:
            text += f" [{', '.join(sources)}]"
        _log.info(f"Scraped *** {len(self.data.decks)} deck(s) *** in total for {text}")
        self._session.bump_channel()

    @timed("channel scraping")
    def scrape_videos(self, *video_ids: str) -> None:
        """Scrape videos of this channel according to the passed YouTube IDs.
        """
        self._scrape_videos(*video_ids)

    @timed("channel scraping")
    def scrape(self, limit=10, only_newer_than_last_scraped=True, soft_limit=False) -> None:
        """Scrape yet unscraped videos of this channel.

        The parameters of this method guard against scraping the whole channel with potentially
        an enormous number of featured videos (think LegenVD with 2.2K, CGB with 3.7K,
        or MTGGoldfish with 7.4K).

        If ``soft_limit`` is set to True, then the limit is extended as needed (so long as it's
        exactly met). This is useful for scraping channels with unusually high number of
        regularly posted material. In this scenario, the ``only_newer_than_last_scraped`` flag
        should be set to True, otherwise the whole channel is going to be scraped.

        Args:
            limit: maximum number of video to attempt scraping
            only_newer_than_last_scraped: if True, only scrape videos newer than the recently scraped
            soft_limit: if True, extend the videos limit indefinitely (unless not exactly met)
        """
        video_ids = self.get_unscraped_video_ids(limit, only_newer_than_last_scraped, soft_limit)
        text = self.url_title_text()
        if not video_ids:
            _log.info(f"Channel data for {text} already up to date")
            return
        self._scrape_videos(*video_ids)

    def dump(self, dstdir: PathLike = "", filename="") -> None:
        """Dump data to a .json file.

        Args:
            dstdir: optionally, the destination directory (if not provided CWD is used)
            filename: optionally, a custom filename (if not provided a default is used)
        """
        if not self.data:
            _log.warning("Nothing to dump")
            return
        dstdir = dstdir or CHANNELS_DIR / self._yt_id
        dstdir = get_dir(dstdir)
        timestamp = self._scrape_time.strftime(FILENAME_TIMESTAMP_FORMAT)
        filename = filename or f"{self._yt_id}___{timestamp}_channel"
        dst = dstdir / f"{sanitize_filename(filename)}.json"
        _log.info(f"Exporting channel to: '{dst}'...")
        dst.write_text(self.data.json, encoding="utf-8")


# CONVENIENCE FUNCTIONS


@http_requests_counted("channel videos scraping")
@timed("channel videos scraping")
def scrape_channel_videos(session: ScrapingSession, channel_id: str, *video_ids: str) -> bool:
    """Scrape specified videos of a YouTube channel in a session.

    Scraped channel's data is saved in a .json file and session ensures decklists are saved
    in global decklists repositories.

    Args:
        session: a scraping session context manager
        channel_id: YouTube ID of a channel to scrape
        *video_ids: YouTube IDs of videos to scrape
    """
    try:
        ch = ChannelScraper(channel_id, session)
        _log.info(f"Scraping {len(video_ids)} video(s) from channel {ch.url_title_text()}...")
        ch.scrape_videos(*video_ids)
        if ch.data:
            ch.dump()
    except Exception as err:
        _log.error(f"Scraping of channel {channel_id!r} failed with: {err!r}")
        _log.error(traceback.format_exc())
        return False

    return True


@http_requests_counted("channels scraping")
@timed("channels scraping")
def scrape_channels(
        *chids: str,
        videos_limit=25,
        only_newer_than_last_scraped=True,
        soft_limit=False) -> None:
    """Scrape YouTube channels as specified in a session.

    Each scraped channel's data is saved in a .json file and session ensures decklists are saved
    in global decklists repositories.

    Args:
        chids: IDs of channels to scrape
        videos_limit: number of videos to scrape per channel
        only_newer_than_last_scraped: if True, only scrape videos newer than the last one scraped
        soft_limit: if True, extend the limit indefinitely unless not exactly met
    """
    with ScrapingSession() as session:
        for i, chid in enumerate(chids, start=1):
            try:
                ch = ChannelScraper(chid, session)
                _log.info(f"Scraping channel {i}/{len(chids)}: {ch.url_title_text()}...")
                ch.scrape(videos_limit, only_newer_than_last_scraped, soft_limit)
                if ch.data:
                    ch.dump()
            except Exception as err:
                _log.error(f"Scraping of channel {chid!r} failed with: {err!r}. Skipping...")
                _log.error(traceback.format_exc())


# TODO: use load_channels() instead
def scrape_fresh(only_deck_fresh=True) -> None:
    """Scrape those YouTube channels saved in a private Google Sheet that are not active,
    dormant nor abandoned.
    """
    ids, retrieved = [], retrieve_ids()
    for chid in tqdm(retrieved, total=len(retrieved), desc="Loading channels data..."):
        try:
            with logging_disabled(logging.ERROR):
                data = load_channel(chid)
        except FileNotFoundError:
            data = None
        if not data:
            ids.append(chid)
        elif data.is_fresh:
            if only_deck_fresh and not data.is_deck_fresh:
                continue
            ids.append(chid)
    text = "fresh and deck-fresh" if only_deck_fresh else "fresh"
    _log.info(f"Scraping {len(ids)} {text} channel(s)...")
    scrape_channels(
        *ids, videos_limit=VIDEOS_COUNT, only_newer_than_last_scraped=True, soft_limit=True)


def scrape_active(only_deck_fresh=True) -> None:
    """Scrape those YouTube channels saved in a private Google Sheet that are active.
    """
    ids, retrieved = [], retrieve_ids()
    for chid in tqdm(retrieved, total=len(retrieved), desc="Loading channels data..."):
        try:
            with logging_disabled(logging.ERROR):
                data = load_channel(chid)
        except FileNotFoundError:
            data = None
        if data and data.is_active:
            if only_deck_fresh and not data.is_deck_fresh:
                continue
            ids.append(chid)
    text = "active and deck-fresh" if only_deck_fresh else "active"
    _log.info(f"Scraping {len(ids)} {text} channel(s)...")
    scrape_channels(
        *ids, videos_limit=VIDEOS_COUNT, only_newer_than_last_scraped=True, soft_limit=True)


def scrape_dormant(only_deck_fresh=True) -> None:
    """Scrape those YouTube channels saved in a private Google Sheet that are dormant.
    """
    ids, retrieved = [], retrieve_ids()
    for chid in tqdm(retrieved, total=len(retrieved), desc="Loading channels data..."):
        try:
            with logging_disabled(logging.ERROR):
                data = load_channel(chid)
        except FileNotFoundError:
            data = None
        if data and data.is_dormant:
            if only_deck_fresh and not data.is_deck_fresh:
                continue
            ids.append(chid)
    text = "dormant and deck-fresh" if only_deck_fresh else "dormant"
    _log.info(f"Scraping {len(ids)} {text} channel(s)...")
    scrape_channels(
        *ids, videos_limit=VIDEOS_COUNT, only_newer_than_last_scraped=True, soft_limit=True)


def scrape_abandoned(only_deck_fresh=True) -> None:
    """Scrape those YouTube channels saved in a private Google Sheet that are abandoned.
    """
    ids, retrieved = [], retrieve_ids()
    for chid in tqdm(retrieved, total=len(retrieved), desc="Loading channels data..."):
        try:
            with logging_disabled(logging.ERROR):
                data = load_channel(chid)
        except FileNotFoundError:
            data = None
        if data and data.is_abandoned:
            if only_deck_fresh and not data.is_deck_fresh:
                continue
            ids.append(chid)
    text = "abandoned and deck-fresh" if only_deck_fresh else "abandoned"
    _log.info(f"Scraping {len(ids)} {text} channel(s)...")
    scrape_channels(
        *ids, videos_limit=VIDEOS_COUNT, only_newer_than_last_scraped=True, soft_limit=True)


def scrape_deck_stale(only_fresh_or_active=True) -> None:
    """Scrape those YouTube channels saved in a private Google Sheet that are considered
    deck-stale.
    """
    ids, retrieved = [], retrieve_ids()
    for chid in tqdm(retrieved, total=len(retrieved), desc="Loading channels data..."):
        try:
            with logging_disabled(logging.ERROR):
                data = load_channel(chid)
        except FileNotFoundError:
            data = None
        if data and data.is_deck_stale:
            if only_fresh_or_active and not (data.is_fresh or data.is_active):
                continue
            ids.append(chid)
    text = "deck-stale and fresh/active" if only_fresh_or_active else "deck-stale"
    _log.info(f"Scraping {len(ids)} {text} channel(s)...")
    scrape_channels(
        *ids, videos_limit=VIDEOS_COUNT, only_newer_than_last_scraped=True, soft_limit=True)


def scrape_very_deck_stale(only_fresh_or_active=True) -> None:
    """Scrape those YouTube channels saved in a private Google Sheet that are considered
    very deck-stale.
    """
    ids, retrieved = [], retrieve_ids()
    for chid in tqdm(retrieved, total=len(retrieved), desc="Loading channels data..."):
        try:
            with logging_disabled(logging.ERROR):
                data = load_channel(chid)
        except FileNotFoundError:
            data = None
        if data and data.is_very_deck_stale:
            if only_fresh_or_active and not (data.is_fresh or data.is_active):
                continue
            ids.append(chid)
    text = "very deck-stale and fresh/active" if only_fresh_or_active else "very deck-stale"
    _log.info(f"Scraping {len(ids)} {text} channel(s)...")
    scrape_channels(
        *ids, videos_limit=VIDEOS_COUNT, only_newer_than_last_scraped=True, soft_limit=True)


def scrape_excessively_deck_stale(only_fresh_or_active=True) -> None:
    """Scrape those YouTube channels saved in a private Google Sheet that are considered
    excessively deck-stale.
    """
    ids, retrieved = [], retrieve_ids()
    for chid in tqdm(retrieved, total=len(retrieved), desc="Loading channels data..."):
        try:
            with logging_disabled(logging.ERROR):
                data = load_channel(chid)
        except FileNotFoundError:
            data = None
        if data and data.is_excessively_deck_stale:
            if only_fresh_or_active and not (data.is_fresh or data.is_active):
                continue
            ids.append(chid)
    text = "excessively deck-stale and fresh/active" if only_fresh_or_active else (
        "excessively deck-stale")
    _log.info(f"Scraping {len(ids)} {text} channel(s)...")
    scrape_channels(
        *ids, videos_limit=VIDEOS_COUNT, only_newer_than_last_scraped=True, soft_limit=True)


def scrape_all() -> None:
    scrape_fresh()
    scrape_active()
    scrape_dormant()
    scrape_abandoned()
    scrape_deck_stale()
    scrape_very_deck_stale()
    scrape_excessively_deck_stale()


