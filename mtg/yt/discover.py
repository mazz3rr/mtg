"""

    mtg.yt.discover
    ~~~~~~~~~~~~~~~
    Discover new YouTube channels to scrape.

    @author: mazz3rr

"""
import logging
from dataclasses import dataclass
from typing import Literal

from youtubesearchpython import CustomSearch, VideoSortOrder, VideoUploadDateFilter

from mtg.data.common import retrieve_ids

_log = logging.getLogger(__name__)
_QUERY_EXCLUDES = (
    '-"altered tcg"',
    '-"curiosa.io"',
    '-dfiance',
    '-digimon',
    '-"eternal card game"',
    '-elestrals',
    '-elestralstcg',
    '-fab',
    '-"fabrary.net"',
    '-"fabtcg.com"',
    '-"flesh and blood"',
    '-"grand archive tcg"',
    '-hptcg',
    '-"lackeybot.com"',
    '-lorcana',
    '-"lotr lcg"',
    '-msem',
    '-onepiece',
    '-pokemon',
    '-"ringsdb.com"',
    '-snap',
    '-"sorcery tcg"',
    '-starwarsunlimited',
    '-"star wars unlimited"',
    '-"swudb.com"',
    '-"ygom.untapped.gg"',
    '-yugioh',
)


def discover_new_channels(
        query: str = "mtg decklist",
        limit=200,
        option: Literal[
            "relevance",
            "upload_date",
            "view_count",
            "rating",
            "last_hour",
            "today",
            "this_week",
            "this_month",
            "this_year"] = "this_week",
        ) -> tuple[list[str], list[str], list[str]]:
    """Discover channels that aren't yet included in the private Google Sheet.

    Args:
        query: YouTube search query (e.g. 'mtg' or 'mtg foo')
        limit: maximum number of videos for 'youtubesearchpython' to return
        option: search option (see: https://pypi.org/project/youtube-search-python/)

    Returns:
        discovered channel IDs, newly-discovered and all checked video links
    """
    query += f' {" ".join(_QUERY_EXCLUDES)}'
    match option:
        case "relevance":
            pref = VideoSortOrder.relevance
        case "upload_date":
            pref = VideoSortOrder.uploadDate
        case "view_count":
            pref = VideoSortOrder.viewCount
        case "rating":
            pref = VideoSortOrder.rating
        case "last_hour":
            pref = VideoUploadDateFilter.lastHour
        case "today":
            pref = VideoUploadDateFilter.today
        case "this_week":
            pref = VideoUploadDateFilter.thisWeek
        case "this_month":
            pref = VideoUploadDateFilter.thisMonth
        case "this_year":
            pref = VideoUploadDateFilter.thisYear
        case _:
            raise ValueError(f"Unsupported search option: {option!r}")

    retrieved_ids, chids = {*retrieve_ids(), *retrieve_ids("withdrawn")}, set()
    results = []
    search = CustomSearch(query, pref)
    while True:
        results += search.result()["result"]
        limit -= 20
        if limit <= 0:
            break
        search.next()

    found_links = []
    for result in results:
        chid = result["channel"]["id"]
        if chid not in retrieved_ids and chid not in chids:
            _log.info(
                f"Found new channel: {chid!r} (video: {result['link']!r})")
            chids.add(chid)
            found_links.append(result["link"])

    _log.info(f"Found {len(chids)} new channel(s) among {len(results)} checked result(s)")

    return sorted(chids), found_links, [r["link"] for r in results]


# TODO: make default limit a shared global constant
@dataclass(frozen=True)
class UrlHook:
    """Encapsulate data needed for discovering new YT deck-featuring channels with queries to
    Google servers.

        positives - positive elements of a query, e .g. "mtg" and  "decklist" in "mtg decklist"
        negatives - negative elements of a query, e.g. "-fab" and "-yugioh" in "mtg decklist -fab -yugioh"
        limit - maximum number of videos for 'youtubesearchpython' to return when querying Google
                servers (default maybe too low for a popular site like Archidekt or Goldfish,
                this needs to be estimated after testing)
    """
    positives: tuple[str, ...]
    negatives: tuple[str, ...] = ()
    limit: int = 200
