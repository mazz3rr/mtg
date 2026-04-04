"""

    mtg.lib.scrape.core
    ~~~~~~~~~~~~~~~~~~~
    Core scraping utilities.

    @author: mazz3rr

"""
import contextlib
import json
import logging
import random
import re
import time
import urllib.parse
from dataclasses import dataclass
from datetime import date, datetime
from functools import wraps
from typing import Callable, Iterator, Self, Type

import brotli
import requests
from bs4 import BeautifulSoup, Tag
from bs4.dammit import EncodingDetector
from requests import HTTPError, Response
from requests.adapters import HTTPAdapter
from urllib3 import Retry

from mtg.constants import Json
from mtg.lib.time import timed

_log = logging.getLogger(__name__)

REQUESTS_TIMEOUT = 15.0  # seconds
DEFAULT_THROTTLING = 1.0  # seconds


class ScrapingError(OSError):
    """Raised whenever scraping produces unexpected results.
    """
    @property
    def scraper(self) -> Type | None:
        return self._scraper

    @property
    def url(self) -> str | None:
        return self._url

    def __init__(
            self, message="No page soup", scraper: Type | None = None, url: str | None = None) -> \
            None:
        self._scraper, self._url = scraper, url
        scraper = scraper.__name__ if scraper else ""
        details = [item for item in (scraper, url) if item]
        if details:
            message += f" [{', '.join(details)}]"
        super().__init__(message)


class InaccessiblePage(ScrapingError):
    """Raised on encountering hidden, private, or otherwise inaccessible pages.
    """
    def __init__(
            self, message="Page hidden, private or otherwise inaccessible",
            scraper: Type | None = None,
            url: str | None = None) -> None:
        super().__init__(message, scraper, url)


class Soft404Error(ScrapingError):
    """Raised on encountering "soft 404 error" pages instead of a decklist page.

    What "soft 404s" are: https://en.wikipedia.org/wiki/HTTP_404
    """
    def __init__(
            self, message="Got Soft 404 (Page Not Found) page instead of an intended one",
            scraper: Type | None = None,
            url: str | None = None) -> None:
        super().__init__(message, scraper, url)


# FIXME: this aren't all HTTP requests done so the name is misleading, a "fetches done" or
#  something like this would be better
_http_requests_count = 0


def handle_brotli(response: Response, return_json: bool = False) -> str | Json:
    if response.headers.get("Content-Encoding") == "br":
        with contextlib.suppress(brotli.error):
            decompressed = brotli.decompress(response.content)
            if return_json:
                return json.loads(decompressed)
            return decompressed
    return response.text


@timed("fetching")
def fetch(
        url: str, postdata: Json | None = None, handle_http_errors=True,
        request_timeout=REQUESTS_TIMEOUT,
        **requests_kwargs) -> Response | None:
    """Do a GET (or POST wit ``postdata``) HTTP request for ``url`` and return the response
    (or None).
    """
    _log.info(f"Fetching: '{url}'...")
    global _http_requests_count
    if postdata:
        response = requests.post(url, json=postdata, **requests_kwargs)
    else:
        response = requests.get(url, timeout=request_timeout, **requests_kwargs)
    _http_requests_count += 1
    if handle_http_errors:
        if str(response.status_code)[0] in ("4", "5"):
            msg = f"Request for '{url}' failed with: '{response.status_code} {response.reason}'"
            if response.status_code in (502, 503, 504):
                raise HTTPError(msg)
            _log.warning(msg)
            return None

    return response


def fetch_json(url: str, handle_http_errors=True, **requests_kwargs) -> Json:
    """Do a GET HTTP request for ``url`` and return the response's JSON data (or an empty dict).
    """
    response = fetch(url, handle_http_errors=handle_http_errors, **requests_kwargs)
    if not response:
        return {}
    return response.json() if response.text else {}


def fetch_soup(
        url: str, headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        request_timeout=REQUESTS_TIMEOUT) -> BeautifulSoup | None:
    """Do a GET HTTP request for ``url`` and return a BeautifulSoup object (or None).

    Args:
        url: URL string
        headers: a dictionary of headers to add to the request
        params: URL's query parameters (if not already present in the URL)
        request_timeout: request timeout in seconds

    Returns:
        a BeautifulSoup object or None on client-side errors
    """
    response = fetch(url, headers=headers, params=params, request_timeout=request_timeout)
    if not response or not response.text:
        return None
    http_encoding = response.encoding if 'charset' in response.headers.get(
        'content-type', '').lower() else None
    html_encoding = EncodingDetector.find_declared_encoding(response.content, is_html=True)
    encoding = html_encoding or http_encoding
    return BeautifulSoup(response.content, "lxml", from_encoding=encoding)


def find_next_sibling_tag(tag: Tag) -> Tag | None:
    for sibling in tag.next_siblings:
        if isinstance(sibling, Tag):
            return sibling
    return None


def find_previous_sibling_tag(tag: Tag) -> Tag | None:
    for sibling in tag.previous_siblings:
        if isinstance(sibling, Tag):
            return sibling
    return None


@dataclass
class Throttling:
    delay: float
    offset: float

    def __mul__(self, factor: float) -> Self:
        return Throttling(self.delay * factor, self.offset * factor)

    def __imul__(self, factor: float) -> Self:
        return Throttling(self.delay * factor, self.offset * factor)

    def __iter__(self) -> Iterator[float]:
        return iter((self.delay, self.offset))


def throttle(delay: float, offset=0.0) -> None:
    if offset:
        delay = round(random.uniform(delay - offset / 2, delay + offset / 2), 3)
    _log.info(f"Throttling for {delay} seconds...")
    time.sleep(delay)


def throttle_with_countdown(delay_seconds: int) -> None:
    for i in range(delay_seconds, 0, -1):
        print(f"Waiting {i} seconds before next batch...", end="\r")
        time.sleep(1)
    print("Ready for next batch!")


def throttled(delay: float, offset=0.0) -> Callable:
    """Add throttling delay after the decorated operation.

    Args:
        delay: throttling delay in fraction of seconds
        offset: randomization offset of the delay in fraction of seconds

    Returns:
        the decorated function
    """
    def decorate(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            result = func(*args, **kwargs)
            throttle(delay, offset)
            return result
        return wrapper
    return decorate


@throttled(DEFAULT_THROTTLING)
def fetch_throttled_soup(url: str, headers: dict[str, str] | None = None) -> BeautifulSoup | None:
    return fetch_soup(url, headers=headers)


def http_requests_counted(operation="") -> Callable:
    """Count HTTP requests done by the decorated operation.

    Args:
        name of the operation

    Returns:
        the decorated function
    """
    def decorate(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            initial_count = _http_requests_count
            result = func(*args, **kwargs)
            requests_made = _http_requests_count - initial_count
            nonlocal operation
            operation = operation or f"{func.__name__!r}"
            _log.info(f"Needed {requests_made} HTTP request(s) to carry out {operation}")
            return result
        return wrapper
    return decorate


@timed("unshortening")
def unshorten(url: str) -> str | None:
    """Unshorten URL shortened by services like bit.ly, tinyurl.com etc.

    courtesy of Phind AI
    """
    # set up retry mechanism
    retries = Retry(total=5, backoff_factor=0.1, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retries)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    try:
        # set a reasonable timeout
        timeout = 10

        # add a User-Agent header to mimic a real browser
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

        # perform GET request instead of HEAD
        resp = session.get(url, allow_redirects=True, timeout=timeout, headers=headers)

        # check if the final URL is different from the original
        if resp.url != url:
            return resp.url
        else:
            # if no redirect occurred, try to parse the HTML for potential JavaScript redirects
            match = re.search(r'window\.location\.href\s*=\s*"([^"]+)"', resp.text)
            if match:
                return match.group(1)

        return None

    except requests.exceptions.SSLError:
        _log.warning(f"Unshortening of {url!r} failed with SSL error")
        return None
    except requests.exceptions.TooManyRedirects:
        _log.warning(f"Unshortening of {url!r} failed due too many redirections")
        return None
    except requests.exceptions.RequestException as e:
        _log.warning(f"Unshortening of {url!r} failed with: {e!r}")
        return None


def extract_url(text: str, https=True) -> str | None:
    """Extract (the first occurrence of) URL from ``text``.

    Pilfered from: https://stackoverflow.com/a/840110/4465708
    """
    pattern = r"(?P<url>https?://[^\s'\"]+)" if https else r"(?P<url>http?://[^\s'\"]+)"
    match = re.search(pattern, text)
    if not match:
        return None
    url = match.group("url").rstrip(",.[](){}/\u2060")
    if url.count("https://") > 1:
        return "https://" + [part for part in url.split("https://") if part][0]
    elif url.count("http://") > 1:
        return "http://" + [part for part in url.split("http://") if part][0]
    elif all(not url.startswith(t) for t in ("https://", "http://")) or len(url) < 10:
        return None
    return url


def dissect_js(
        tag: Tag, start_hook: str, end_hook="",
        end_processor: Callable[[str], str] | None = None,
        left_split_on_start_hook=False) -> Json | None:
    """Dissect JSON from JavaScript in a <script> tag by the passed hooks.

    If the passed tag is not a <script>, then it's searched first among the passed tag's
    descendants for containment of the passed hooks.
    """
    if tag.name == "script":
        script_tag = tag
    else:
        if end_hook:
            script_tag = tag.find(
                "script", string=lambda s: s and start_hook in s and end_hook in s)
        else:
            script_tag = tag.find(
                "script", string=lambda s: s and start_hook in s)
    if not script_tag:
        return None

    text = script_tag.text
    if left_split_on_start_hook:
        _, first = text.split(start_hook, maxsplit=1)
    else:
        *_, first = text.split(start_hook)
    if end_hook:
        second, *_ = first.split(end_hook)
        json_text = second
    else:
        json_text = first
    if end_processor:
        json_text = end_processor(json_text)
    return json.loads(json_text)


def strip_url_query(url: str, keep_fragment=False) -> str:
    """Strip query parameters from the URL.

    https://www.youtube.com/watch?v=93gF1q7ey84 ==> https://www.youtube.com/watch
    https://deckstats.net/?lng=en ==> https://deckstats.net

    Args:
        url: URL to be stripped
        keep_fragment: whether to keep the fragment part of the URL

    Returns:
        URL with query parameters removed
    """
    # split the URL into its components
    parsed_url = urllib.parse.urlsplit(url)

    # reconstruct the URL without query parameters
    stripped_url = urllib.parse.urlunsplit((
        parsed_url.scheme,
        parsed_url.netloc,
        parsed_url.path.removesuffix('/'),  # remove any trailing slash
        '',  # remove query
        parsed_url.fragment if keep_fragment else ''  # keep or remove fragment
    ))

    return stripped_url.removesuffix("/")


def get_netloc_domain(url: str) -> str:
    """Return the netloc domain of the supplied URL.

    E.g. supplying 'https://www.hareruyamtg.com/decks/1043414?utm_source=video' results in:
        'www.hareruyamtg.com'
    """
    try:
        return urllib.parse.urlsplit(url).netloc
    except ValueError:
        return ""


def get_query_values(url: str, param: str) -> list[str]:
    """Return query parameter values from supplied URL. If URL is invalid, or on any other failure,
    return an empty list.

    E.g. supplying 'https://www.hareruyamtg.com/decks/1023318?display_token=f9d56.d861dfb19d83d9' and 'display_token' results in:
        ["f9d56.d861dfb19d83d9"]
    """
    try:
        query = urllib.parse.urlsplit(url).query
    except ValueError:
        return []
    return urllib.parse.parse_qs(query).get(param, []) if query else []


def get_path_segments(url: str) -> list[str]:
    """Return path segments from supplied URL.

    E.g. supplying 'https://www.hareruyamtg.com/decks/1043414?utm_source=video' results in:
        ["decks", "1043414"]
        and supplying 'https://www.hareruyamtg.com' or 'https://www.hareruyamtg.com/' results in: []
    """
    try:
        path = urllib.parse.urlsplit(url).path.strip("/")
    except ValueError:
        return []
    return path.split("/") if path else []


def url_decode(encoded: str) -> str:
    """Decode URL-encoded string.

    Example:
        ""Virtue+of+Loyalty+%2F%2F+Ardenvale+Fealty"" ==> "Virtue of Loyalty // Ardenvale Fealty"
    """
    return urllib.parse.unquote(encoded.replace('+', ' '))


def is_more_than_root_path(url: str, root_path: str, case_insensitive=True) -> bool:
    """Check whether the passed URL is more than the provided root path (whether the root path is
    within the URL, but NOT EXACTLY it).

    Args:
        url: a URL to check
        root_path: URL root path (netloc + (optionally) initial path segments, with or without
                   the trailing slash), e.g. "pauperwave.com" or "playingmtg.com/tournaments/")
        case_insensitive: if True, make the check case-insensitive
    """
    url = url.lower() if case_insensitive else url
    root_path = root_path.lower() if case_insensitive else root_path
    url = url.removesuffix("/") + "/"
    root_path = root_path.removesuffix("/") + "/"
    if root_path not in url:
        return False  # root path not within URL
    *_, rest = url.split(f"{root_path}")
    if not rest:
        return False  # URL is the root path exactly
    return True


MONTHS = [
    'January',
    'February',
    'March',
    'April',
    'May',
    'June',
    'July',
    'August',
    'September',
    'October',
    'November',
    'December'
]


def parse_non_english_month_date(date_text: str, *months: str) -> date:
    """Parse a datetime.date object from a date text containing a non-English month.

    Args:
        date_text: date text to be parsed
        months: non-English month names (from January to December)
    """
    if not len(months) == 12:
        raise ValueError(f"Expected 12 months, got {len(months)}")
    month_smap = {m1.title(): m2 for m1, m2 in zip(months, MONTHS)}
    day, month, year = date_text.split()
    day = day.strip('.')
    if month in MONTHS:
        english_month = month
    else:
        # convert month to English
        english_month = month_smap.get(month)
        if not english_month:
            raise ValueError(f"Unknown month: {month}")
    # create a date string in a format that can be parsed by strptime
    english_date_string = f"{day} {english_month} {year}"
    # parse the date
    return datetime.strptime(english_date_string, "%d %B %Y").date()


def prepend_url(url: str, prefix="") -> str:
    """Prepend ``url`` with the prefix provided (only if it's not already there).
    """
    if prefix:
        return f"{prefix}{url}" if not (url.startswith(prefix) or url.startswith("http")) else url
    return url


def find_links(
        *tags: Tag, css_selector="", url_prefix="", query_stripped=False,
        **bs_options) -> list[str]:
    """Find all links in the provided tags.

        Args:
            *tags: variable number of BeautifulSoup tags containing links
            css_selector: CSS selector to pass to BeautifulSoup's select() method
            url_prefix: prefix to prepend relative URLs with
            query_stripped: whether to strip the query part of the URL
            **bs_options: options to pass to BeautifulSoup's find_all() method for better filtering
        """
    links = set()
    for tag in tags:
        if css_selector:
            links |= {t.attrs["href"].removesuffix("/") for t in tag.select(css_selector)}
        else:
            bs_options = bs_options or {"href": lambda h: h}
            links |= {t.attrs["href"].removesuffix("/") for t in tag.find_all("a", **bs_options)}
    links = {prepend_url(l, url_prefix) for l in links} if url_prefix else links
    links = {strip_url_query(l) for l in links} if query_stripped else links
    return sorted(links)


def _parse_double_quoted_keywords(kw_text: str) -> list[str]:
    """Parse passed keyword string into keywords.

    Example string:
        `"Magic the gathering" MTG "magic arena" arena "mtg arena" standard brawl commander edh deck "standard deck" "how to" "card game" "deck build" pioneer histori...`
    """
    keywords = []
    tokens = [t for t in kw_text.split('"') if t]
    # tokens that start with whitespace after splitting by double-quotes and filtering
    # for empty strings are actually multiple keywords and need further splitting
    for t in tokens:
        if t.startswith(" "):
            t = t.strip()
            if " " in t:
                keywords.extend(t.split())
            else:
                keywords.append(t)
        else:
            keywords.append(t)
    return keywords


def parse_keywords_from_tag(tag: Tag) -> list[str]:
    """Parse passed tag's content attribute string for keyword string tokens.

    Expected string should fall into two categories:
        * with keywords separated by whitespace and multiword keywords surrounded in double-quotes
          e.g.: `"Magic the gathering" MTG "magic arena" arena "mtg arena" histori...`
        * with comma-separated keywords, e.g.: `video, sharing, camera phone, video phone, free`
    """
    if kw_text := tag.get("content", ""):
        if '"' in kw_text:
            keywords = _parse_double_quoted_keywords(kw_text)
        elif ", " in kw_text:
            keywords = kw_text.split(", ")
        else:
            keywords = [kw_text]
    else:
        keywords = []
    # discard any unfinished keyword
    if keywords and keywords[-1].endswith('...'):
        keywords.pop()
    return keywords
