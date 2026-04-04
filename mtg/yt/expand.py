"""

    mtg.yt.expand
    ~~~~~~~~~~~~~
    Expand links in YT videos descriptions for deck-scraping.

    @author: mazz3rr

"""
import logging

import backoff
from bs4 import Tag
from requests import ConnectionError, HTTPError, ReadTimeout
from selenium.common import TimeoutException

from mtg.session import UrlsStateManager
from mtg.lib.scrape.core import dissect_js, fetch
from mtg.lib.scrape.dynamic import fetch_dynamic_soup

_log = logging.getLogger(__name__)


# TODO: async, Dropbox (#376)
# FIXME: halt Selenium waits on finding sentinel tags (#378)
class LinksExpander:
    """Expand links to prospective pages into lines eligible for deck-processing.

    So far supports:
        * Pastebin-like links (Pastebin, GitHub Gist)
        * Google Docs
        * Patreon posts
    """
    # on 15th Jan 2025 there were only 108 `pastebin.com` and 2 `gist.github.com` links
    # identified across 278,101 links scraped so far from YT videos' descriptions in total.
    PASTEBIN_LIKE_HOOKS = {
        "gist.github.com/",
        "pastebin.com/",
    }
    OBSCURE_PASTEBIN_LIKE_HOOKS = {
        "bitbin.it/",
        "bpa.st/",
        "cl1p.net/",
        "codebeautify.org/",
        "codeshare.io/",
        "commie.io/",
        "controlc.com/",
        "cutapaste.net/",
        "defuse.ca/pastebin.htm/",
        "dotnetfiddle.net/",
        "dpaste.com/",
        "dpaste.org/",
        "everfall.com/paste/",
        "friendpaste.com/",
        "hastebin.com/",
        "ide.geeksforgeeks.org/",
        "ideone.com/",
        "ivpaste.com/",
        "jpst.it/",
        "jsbin.com/",
        "jsfiddle.net/",
        "jsitor.com/",
        "justpaste.it/",
        "justpaste.me/",
        "kpaste.net/",
        "n0paste.tk/",
        "nekobin.com/",
        "notes.io/",
        "p.ip.fi/",
        "paste-bin.xyz/",
        "paste.centos.org/",
        "paste.debian.net/",
        "paste.ee/",
        "paste.jp/",
        "paste.mozilla.org/",
        "paste.ofcode.org/",
        "paste.opensuse.org/",
        "paste.org.ru/",
        "paste.rohitab.com/",
        "paste.sh/",
        "paste2.org/",
        "pastebin.ai/",
        "pastebin.fi/",
        "pastebin.fr/",
        "pastebin.osuosl.org/",
        "pastecode.io/",
        "pasted.co/",
        "pasteio.com/",
        "pastelink.net/",
        "pastie.org/",
        "privatebin.net/",
        "pst.innomi.net/",
        "quickhighlighter.com/",
        "termbin.com/",
        "tny.cz/",
        "tutpaste.com/",
        "vpaste.net/",
        "www.paste.lv/",
        "www.paste4btc.com/",
        "www.pastebin.pt/",
    }
    _PATREON_XPATH = "//div[contains(@class, 'sc-dtMgUX') and contains(@class, 'IEufa')]"
    _PATREON_XPATH2 = "//div[contains(@class, 'sc-b20d4e5f-0') and contains(@class, 'fbPSoT')]"
    _GOOGLE_DOC_XPATH = "//div[@id='docs-editor-container']"

    @property
    def expanded_links(self) -> list[str]:
        return self._expanded_links

    @property
    def gathered_links(self) -> list[str]:
        return self._gathered_links

    @property
    def lines(self) -> list[str]:
        return self._lines

    def __init__(self, *links: str) -> None:
        self._urls_manager = UrlsStateManager()
        self._links = [l for l in links if not self._urls_manager.is_failed(l)]
        self._expanded_links, self._gathered_links, self._lines = [], [], []
        self._expand()

    @classmethod
    def is_pastebin_like_url(cls, url: str) -> bool:
        return any(h in url for h in cls.PASTEBIN_LIKE_HOOKS)

    @classmethod
    def is_obscure_pastebin_like_url(cls, url: str) -> bool:
        return any(h in url for h in cls.OBSCURE_PASTEBIN_LIKE_HOOKS)

    @backoff.on_exception(
        backoff.expo, (ConnectionError, HTTPError, ReadTimeout), max_time=60)
    def _expand(self) -> None:
        for link in self._links:
            if self.is_pastebin_like_url(link):
                _log.info(f"Expanding {link!r}...")
                self._expand_pastebin(link)
            elif self.is_obscure_pastebin_like_url(link):
                _log.warning(f"Obscure pastebin-like link found: {link!r}...")
            elif self.is_patreon_url(link):
                _log.info(f"Expanding {link!r}...")
                self._expand_patreon(link)
            elif self.is_google_doc_url(link):
                _log.info(f"Expanding {link!r}...")
                self._expand_google_doc(link)

    def _expand_pastebin(self, link: str) -> None:
        original_link = link
        if "gist.github.com/" in link and not link.endswith("/raw"):
            link = f"{link}/raw"
        elif "pastebin.com/" in link and "/raw/" not in link:
            link = link.replace("pastebin.com/", "pastebin.com/raw/")

        response = fetch(link)
        if not response:
            self._urls_manager.add_failed(original_link)
            return

        lines = [l.strip() for l in response.text.splitlines()]
        self._lines += [l.strip() for l in response.text.splitlines()]
        _log.info(f"Expanded {len(lines)} Pastebin-like line(s)")
        self._expanded_links.append(original_link)

    @staticmethod
    def is_patreon_url(url: str) -> bool:
        return "patreon.com/posts/" in url.lower()

    def _get_patreon_text_tag(self, link: str) -> Tag | None:
        try:
            soup, _, _ = fetch_dynamic_soup(link, self._PATREON_XPATH, timeout=10)
            if not soup:
                _log.warning("Patreon post data not available")
                self._urls_manager.add_failed(link)
                return None
            return soup.find("div", class_=lambda c: c and "sc-dtMgUX" in c and 'IEufa' in c)
        except TimeoutException:
            try:
                soup, _, _ = fetch_dynamic_soup(link, self._PATREON_XPATH2)
                if not soup:
                    _log.warning("Patreon post data not available")
                    self._urls_manager.add_failed(link)
                    return None
                return soup.find(
                    "div", class_=lambda c: c and "sc-b20d4e5f-0" in c and 'fbPSoT' in c)
            except TimeoutException:
                _log.warning("Patreon post data not available")
                self._urls_manager.add_failed(link)
                return None

    def _expand_patreon(self, link: str) -> None:
        text_tag = self._get_patreon_text_tag(link)
        if not text_tag:
            return
        lines = [p_tag.text.strip() for p_tag in text_tag.find_all("p")]
        self._lines += lines
        _log.info(f"Expanded {len(lines)} Patreon line(s)")
        self._expanded_links.append(link)

    @staticmethod
    def is_google_doc_url(url: str) -> bool:
        return "docs.google.com/document/" in url.lower()

    def _expand_google_doc(self, link: str) -> None:
        # url = "https://docs.google.com/document/d/1Bnsd4M7n_8LHfN6uEJVxoRr72antIEIO9w4YOGKltiU/edit"
        try:
            soup, _, _ = fetch_dynamic_soup(link, self._GOOGLE_DOC_XPATH)
            if not soup:
                _log.warning("Google Docs document data not available")
                self._urls_manager.add_failed(link)
                return
        except TimeoutException:
            _log.warning("Google Docs document data not available")
            self._urls_manager.add_failed(link)
            return

        start = "DOCS_modelChunk = "
        end = "; DOCS_modelChunkLoadStart = "
        js = dissect_js(soup, start_hook=start, end_hook=end, left_split_on_start_hook=True)

        if not js:
            _log.warning("Google Docs document data not available")
            self._urls_manager.add_failed(link)
            return

        matched_text, links = None, []
        for i, d in enumerate(js):
            match d:
                case {"s": text} if i == 0:
                    matched_text = text.strip()
                case {"sm": {'lnks_link': {'ulnk_url': link}}}:
                    links.append(link)
                    self._gathered_links.append(link)
                case _:
                    pass

        lines = []
        if matched_text:
            lines = [l.strip() for l in matched_text.splitlines()]
            self._lines += lines

        _log.info(f"Expanded {len(lines)} Google Docs line(s) and gathered {len(links)} link(s)")
