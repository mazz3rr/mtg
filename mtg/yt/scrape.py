"""

    mtg.yt.scrape
    ~~~~~~~~~~~~~
    Scrape YouTube videos and channels.

    @author: mazz3rr

"""
import json
import logging
import re
import traceback
import urllib.error
from functools import cached_property
from http.client import RemoteDisconnected
from typing import Iterator

import backoff
import pytubefix
import scrapetube
from requests import ConnectionError, HTTPError, ReadTimeout, Timeout
from tqdm import tqdm
from youtube_comment_downloader import SORT_BY_POPULAR, YoutubeCommentDownloader

from mtg import DeckScraper, DeckUrlsContainerScraper, HybridContainerScraper
from mtg.constants import CHANNELS_DIR, FILENAME_TIMESTAMP_FORMAT, Json, PathLike
from mtg.data.common import load_channel, retrieve_ids
from mtg.data.structures import CHANNEL_URL_TEMPLATE, Channel, SerializedDeck, VIDEO_URL_TEMPLATE, \
    Video
from mtg.deck.arena import ArenaParser, LinesParser
from mtg.deck.core import Deck, DeckParser
from mtg.deck.scrapers.abc import DeckTagsContainerScraper, DecksJsonContainerScraper, \
    get_throttled_deck_scrapers
from mtg.session import CoolOffManager, DecklistsStateManager, ScrapingSession, \
    UrlsStateManager
from mtg.lib.common import find_longest_seqs, from_iterable, logging_disabled
from mtg.lib.numbers import extract_float, multiply_by_symbol
from mtg.lib.time import naive_utc_now as utcnow, timed
from mtg.lib.files import getdir, sanitize_filename
from mtg.lib.scrape.core import ScrapingError, extract_url, http_requests_counted, \
    parse_keywords_from_tag, throttle, throttled, unshorten
from mtg.lib.scrape.dynamic import fetch_dynamic_soup
from mtg.lib.scrape.linktree import LinktreeScraper
from mtg.scryfall import all_formats
from mtg.yt.expand import LinksExpander
from mtg.yt.ptfix import PytubeWrapper

# from yt_dlp import YoutubeDL  # works, but with enormous downtimes (ca. 40s per channel)


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
        return VIDEO_URL_TEMPLATE.format(self._id)

    @cached_property
    def _desc_lines(self) -> list[str]:
        return [
            line.strip() for line in self._description.splitlines()] if self._description else []

    @property
    def deck_metadata(self) -> Json:
        metadata = {}
        if self._derived_format:
            metadata["format"] = self._derived_format
        if self._derived_name:
            metadata["name"] = self._derived_name
        if self._author:
            metadata["author"] = self._author
        if self._publish_time:
            metadata["date"] = self._publish_time.date()
        return metadata

    @property
    def data(self) -> Video | None:
        return self._data

    @property
    def channel_id(self) -> str | None:
        return self._channel_id

    def __init__(self, video_id: str) -> None:
        """Initialize.

        Args:
            video_id: unique string identifying a YouTube video (the part after `v=` in the URL)
        """
        self._urls_manager = UrlsStateManager()
        self._urls_manager.current_video = video_id
        self._decklists_manager = DecklistsStateManager()
        self._cooloff_manager = CoolOffManager()
        self._id = video_id
        # description and title is also available in scrapetube data on Channel abstraction layer
        self._author, self._description, self._title = None, None, None
        self._keywords, self._publish_time, self._views = None, None, None
        self._comment, self._channel_id = None, None
        self._pytube, self._data = None, None

    def _get_pytube(self) -> PytubeWrapper:
        try:
            pytube = pytubefix.YouTube(self.url, use_oauth=True, allow_oauth_cache=True)
        except pytubefix.exceptions.RegexMatchError as rme:
            raise ValueError(f"Invalid video ID: {self._id!r}") from rme
        if not pytube.publish_date:
            raise MissingVideoPublishTime(
                "pytubefix data missing publish time", scraper=type(self), url=self.url)
        wrapper = PytubeWrapper(pytube)
        wrapper.retrieve()
        return wrapper

    @backoff.on_exception(
        backoff.expo,
        (Timeout, HTTPError, RemoteDisconnected, MissingVideoPublishTime, urllib.error.HTTPError),
        max_time=300)
    def _get_pytube_with_backoff(self) -> PytubeWrapper:
        return self._get_pytube()

    def _save_pytube_data(self) -> None:
        self._author = self._pytube.data.author
        self._description = self._pytube.data.description
        self._title = self._pytube.data.title
        self._keywords = self._pytube.data.keywords
        self._publish_time = self._pytube.publish_time
        self._views = self._pytube.data.views
        self._channel_id = self._pytube.channel_id

    def _scrape_video(self) -> None:
        try:
            self._pytube = self._get_pytube()
        except (RemoteDisconnected, ScrapingError) as e:
            _log.warning(
                f"pytubefix had a hiccup ({e}). Retrying with backoff (60 seconds max)...")
            self._pytube = self._get_pytube_with_backoff()
        self._save_pytube_data()

    def _derive_format(self) -> str | None:
        # first, check the keywords
        if self._keywords:
            if fmt := DeckParser.derive_format_from_words(*self._keywords, use_japanese=True):
                return fmt
        # then the title and description
        return DeckParser.derive_format_from_text(
            self._title + self._description, use_japanese=True)

    def _derive_name(self) -> str | None:
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

    @classmethod  # recursive
    def _parse_lines(cls, *lines, expand_links=True) -> tuple[list[str], list[str]]:
        links, other_lines = set(), []
        for line in lines:
            url = extract_url(line)
            if url:
                if cls.is_shortened_url(url):
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
            expander = LinksExpander(*links)
            links = {l for l in links if l not in expander.expanded_links}
            links.update(expander.gathered_links)
            new_links, new_lines = cls._parse_lines(*expander.lines, expand_links=False)
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

    def _process_deck(self, link: str) -> Deck | None:
        deck = None
        if scraper := DeckScraper.from_url(link, self.deck_metadata):
            sanitized_link = scraper.sanitize_url(link)
            if self._urls_manager.is_scraped(sanitized_link):
                _log.info(f"Skipping already scraped deck URL: {sanitized_link!r}...")
                return None
            elif self._urls_manager.is_failed(sanitized_link):
                _log.info(f"Skipping already failed deck URL: {sanitized_link!r}...")
                return None
            try:
                deck = scraper.scrape(throttled=type(scraper) in get_throttled_deck_scrapers())
            except ReadTimeout:
                _log.warning(f"Back-offed scraping of {link!r} failed with read timeout")
            if not deck:
                self._urls_manager.add_failed(sanitized_link)

        if deck:
            deck_name = f"{deck.name!r} deck" if deck.name else "Deck"
            _log.info(f"{deck_name} scraped successfully")
            if deck_url := deck.metadata.get("url"):
                self._urls_manager.add_scraped(deck_url)

        return deck

    def _process_urls(self, *urls: str) -> list[Deck]:
        decks = []
        for url in urls:
            if deck := self._process_deck(url):
                decks.append(deck)
        return decks

    def _process_lines(self, *lines: str) -> list[Deck]:
        decks, lp = [], LinesParser(*lines)
        for decklist in lp.parse():
            if deck := ArenaParser(decklist, self.deck_metadata).parse():
                deck_name = f"{deck.name!r} deck" if deck.name else "Deck"
                _log.info(f"{deck_name} scraped successfully")
                decks.append(deck)
        if not decks:
            if decklists := lp.parse(single_decklist_mode=True):
                if deck := ArenaParser(decklists[0], self.deck_metadata).parse():
                    deck_name = f"{deck.name!r} deck" if deck.name else "Deck"
                    _log.info(f"{deck_name} scraped successfully")
                    decks.append(deck)
        return decks

    def _collect(self, links: list[str], lines: list[str]) -> list[Deck]:
        decks: set[Deck] = set()

        # 1st stage: URLs
        decks.update(self._process_urls(*links))

        # 2nd stage: text decklists
        decks.update(self._process_lines(*lines))

        # 3rd stage: deck containers
        for link in links:
            # skipping of already scraped/failed links for JSON, tag and hybrid scrapers happens
            # here; the same skipping happens for deck URLs scrapers within them per each
            # individual deck URL; skipping for hybrid scrapers happens ONLY if their JSON or tag
            # parts flag the container URL as scraped/failed
            if scraper := DeckUrlsContainerScraper.from_url(
                    link, self.deck_metadata):
                decks.update(scraper.scrape_decks())
            elif scraper := DecksJsonContainerScraper.from_url(
                    link, self.deck_metadata) or DeckTagsContainerScraper.from_url(
                link, self.deck_metadata) or HybridContainerScraper.from_url(
                link, self.deck_metadata):
                sanitized_link = scraper.sanitize_url(link)
                if self._urls_manager.is_scraped(sanitized_link):
                    _log.info(
                        f"Skipping already scraped {scraper.short_name()} URL: "
                        f"{sanitized_link!r}...")
                    continue
                if self._urls_manager.is_failed(sanitized_link):
                    _log.info(
                        f"Skipping already failed {scraper.short_name()} URL: "
                        f"{sanitized_link!r}...")
                    continue
                decks.update(scraper.scrape_decks())

        for deck in decks:
            self._decklists_manager.add_regular(deck.decklist_hash, deck.decklist)
            self._decklists_manager.add_with_printings(
                deck.decklist_with_printings_hash, deck.decklist_with_printings)

        return sorted(decks)

    def _scrape_decks(self) -> None:
        self._derived_format = self._derive_format()
        self._derived_name = self._derive_name()
        links, lines = self._parse_lines(self._title, *self._desc_lines)
        self._decks = self._collect(links, lines)
        if not self._decks:  # try with author's comment
            comment_lines = self._get_comment_lines()
            if comment_lines:
                links, lines = self._parse_lines(*comment_lines)
                self._decks = self._collect(links, lines)
                if self._decks:
                    self._comment = "\n".join(comment_lines)
        self._cooloff_manager.bump_decks(len(self._decks))

    @timed("video scraping")
    @throttled(1.25, 0.25)
    def scrape(self) -> None:
        self._scrape_video()
        self._scrape_decks()
        self._data = Video(
            self._id,
            self._author,
            self._title,
            self._description,
            self._keywords,
            self._publish_time,
            self._views,
            self._comment,
            [SerializedDeck(d.metadata, d.decklist_hash, d.decklist_with_printings_hash) for d in self._decks]
        )

    def get_channel_subscribers(self) -> int | None:
        if not self._pytube:
            return None

        pattern = r'(\d+(?:\.\d+)?)\s*([KMB]?)\s*subscribers'
        match = re.search(pattern, self._pytube.embed_html) or re.search(
            pattern, self._pytube.watch_html)

        if match:
            # extract the number and suffix from the match
            number = float(match.group(1))
            suffix = match.group(2)
            return multiply_by_symbol(number, suffix)

        return None


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
        return CHANNEL_URL_TEMPLATE.format(self._id)

    @property
    def tags(self) -> list[str] | None:
        return sorted(set(self._tags)) if self._tags else None

    @property
    def data(self) -> Channel | None:
        return self._data

    @property
    def earlier_data(self) -> Channel | None:
        return self._earlier_data

    def __init__(self, channel_id: str) -> None:
        self._id = channel_id
        self._cooloff_manager = CoolOffManager()
        self._urls_manager = UrlsStateManager()
        self._urls_manager.current_snapshot = self._id
        self._title, self._subscribers, self._description, self._tags = None, None, None, None
        self._scrape_time, self._videos = None, []
        self._data = None
        self._handle_earlier_data()

    def _handle_earlier_data(self) -> None:
        try:
            self._earlier_data = load_channel(self._id)
            self._title, self._description, self._tags = (
                self._earlier_data.title, self._earlier_data.description, self._earlier_data.tags)
            self._urls_manager.update_scraped({self._id: self.earlier_data.deck_urls})
            self._urls_manager.update_scraped(
                {f"{self._id}/{v.id}": v.deck_urls for v in self.earlier_data.videos})
        except FileNotFoundError:
            self._earlier_data = None

    def video_ids(self, limit=10) -> Iterator[str]:
        try:
            for ch_data in scrapetube.get_channel(channel_id=self._id, limit=limit):
                yield ch_data["videoId"]
        except OSError as ose:
            raise ValueError(f"Invalid channel ID: {self._id!r}") from ose
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
        scraped_ids = [v.id for v in self.earlier_data.videos] if self.earlier_data else []
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
        """Return a list of yet unscraped videos' IDs of this channel.

        See scrape() for the description of the parameters.
        """
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

    # works, but with enormous downtimes (ca. 40s per channel)
    # @timed("fetching channel info")
    # def _fetch_ytdlp_info(self) -> tuple[str | None, int | None, str | None, list[str] | None]:
    #     options = {
    #         'quiet': True,  # suppress verbose output
    #         'extract_flat': True,  # avoid downloading, only fetch metadata
    #         'force_generic_extractor': False,
    #     }
    #     # options = {
    #     #     'quiet': True,
    #     #     'skip_download': True,
    #     #     'no_playlist': True,
    #     #     'extract_flat': True,
    #     # }
    #     with YoutubeDL(options) as ydl:
    #         # fetch channel info
    #         info = ydl.extract_info(self.url, download=False)
    #         # extract relevant metadata
    #         return info.get('title'), info.get('channel_follower_count'), info.get(
    #             'description'), info.get('tags')

    def _fetch_info_with_selenium(
            self) -> tuple[str | None, int | None, str | None, list[str] | None]:
        soup, _, _ = fetch_dynamic_soup(self.url, self.XPATH, consent_xpath=self.CONSENT_XPATH)
        title, description, tags, count = None, None, None, None

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
            tags = parse_keywords_from_tag(keywords_tag)

        # subscribers
        if count_text := soup.find(
            "span", string=lambda t: t and "subscriber" in t).text.removesuffix(
            " subscribers").removesuffix(" subscriber"):
            count = extract_float(count_text)
            if count_text and count_text[-1] in {"K", "M", "B", "T"}:
                count = multiply_by_symbol(count, count_text[-1])
            count = int(count)

        return title, count, description, tags

    def _scrape_videos(self, *video_ids: str) -> None:
        self._scrape_time = utcnow()
        self._title, self._subscribers, self._description, self._tags = self._fetch_info_with_selenium()

        text = self.url_title_text()
        _log.info(f"Scraping channel: {text}, {len(video_ids)} video(s)...")

        self._videos, scraper = [], None
        for i, vid in enumerate(video_ids, start=1):
            _log.info(
                f"Scraping video {i}/{len(video_ids)}: '{VIDEO_URL_TEMPLATE.format(vid)}'...")
            scraper = VideoScraper(vid)
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
            if scraper.channel_id != self._id:
                _log.warning(
                    f"Skipping video with a wrong channel ID: {scraper.channel_id!r} != "
                    f"{self._id!r}")
                continue
            self._videos.append(scraper.data)
            self._cooloff_manager.bump_video()

        if not self._subscribers and scraper:
            self._subscribers = scraper.get_channel_subscribers()
        self._data = Channel(
            id=self._id,
            title=self._title,
            description=self._description,
            tags=self.tags,
            subscribers=self._subscribers,
            scrape_time=self._scrape_time,
            videos=self._videos,
        )
        text = self.url_title_text()
        sources = sorted({d.source for d in self.data.decks if d.source})
        if sources:
            text += f" [{', '.join(sources)}]"
        _log.info(f"Scraped *** {len(self.data.decks)} deck(s) *** in total for {text}")

    @timed("channel scraping")
    def scrape_videos(self, *video_ids: str) -> None:
        """Scrape videos of this channel according to the passed IDs.
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
            _log.info("Nothing to dump")
            return
        dstdir = dstdir or CHANNELS_DIR / self._id
        dstdir = getdir(dstdir)
        timestamp = self._scrape_time.strftime(FILENAME_TIMESTAMP_FORMAT)
        filename = filename or f"{self._id}___{timestamp}_channel"
        dst = dstdir / f"{sanitize_filename(filename)}.json"
        _log.info(f"Exporting channel to: '{dst}'...")
        dst.write_text(self.data.json, encoding="utf-8")
        self._cooloff_manager.bump_channel()


# CONVENIENCE FUNCTIONS


@http_requests_counted("channel videos scraping")
@timed("channel videos scraping")
def scrape_channel_videos(channel_id: str, *video_ids: str) -> bool:
    """Scrape specified videos of a YouTube channel in a session.

    Scraped channel's data is saved in a .json file and session ensures decklists are saved
    in global decklists repositories.

    Args:
        channel_id: ID of a channel to scrape
        *video_ids: IDs of videos to scrape
    """
    try:
        ch = ChannelScraper(channel_id)
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
    with ScrapingSession():
        for i, chid in enumerate(chids, start=1):
            try:
                ch = ChannelScraper(chid)
                _log.info(f"Scraping channel {i}/{len(chids)}: {ch.url_title_text()}...")
                ch.scrape(videos_limit, only_newer_than_last_scraped, soft_limit)
                if ch.data:
                    ch.dump()
            except Exception as err:
                _log.error(f"Scraping of channel {chid!r} failed with: {err!r}. Skipping...")
                _log.error(traceback.format_exc())


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


