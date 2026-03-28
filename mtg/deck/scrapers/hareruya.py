"""

    mtg.deck.scrapers.hareruya
    ~~~~~~~~~~~~~~~~~~~~~~~~~~
    Scrape Hareruya decklists.

    @author: mazz3rr

"""
import logging
import urllib.parse
from typing import override

import dateutil.parser
from bs4 import BeautifulSoup, NavigableString, Tag

from mtg.constants import Json, SECRETS
from mtg.deck.abc import JsonBasedDeckParser, TagBasedDeckParser
from mtg.deck.scrapers.abc import Collected, DeckScraper, DeckUrlsContainerScraper, \
    HybridContainerScraper, throttled_deck_scraper
from mtg.deck.scrapers.goldfish import HEADERS as GOLDFISH_HEADERS
from mtg.lib.common import ParsingError, extract_int
from mtg.lib.scrape.core import ScrapingError, fetch, fetch_json, find_next_sibling_tag, \
    get_path_segments, get_query_values, is_more_than_root_path, strip_url_query
from mtg.yt.discover import UrlHook

_log = logging.getLogger(__name__)


URL_HOOKS = (
    # international deck
    UrlHook(
        ('"hareruyamtg.com/"', '"/deck/"'),
        ('-"/result"', '-"/bulk/"', '-"/metagame"'),
    ),
    # japanese deck
    UrlHook(
        ('"hareruyamtg.com/decks/"', ),
        ('-"/user/"', '-"/tile/"', '-"/search"'),
    ),
    UrlHook(
        ('"deck.hareruyamtg.com/deck/"', ),
        ('-"/user/"', '-"/tile/"', '-"/search"'),
    ),
    # event
    UrlHook(
        ('"hareruyamtg.com/"', '"/deck"', '"/result?"', '"eventName="'),
        limit=100
    ),
    # player
    UrlHook(
        ('"hareruyamtg.com/"', '"/deck"', '"/result?"', '"player="'),
        limit=100
    ),
    # article & author
    UrlHook(
        ('"article.hareruyamtg.com/article/"', ),
    ),
)


@throttled_deck_scraper
@DeckScraper.registered
class InternationalHareruyaDeckScraper(DeckScraper):
    """Scraper of international Hareruya decklist page.
    """
    HEADERS = GOLDFISH_HEADERS  # override

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        url = url.lower()
        return ("hareruyamtg.com" in url and "/deck/" in url
                and "deck.hareruyamtg.com/deck/" not in url  # Japanese deck scraper (look below)
                and "/result" not in url
                and "/bulk/" not in url  # shopping cart URL
                and "/metagame" not in url)

    @staticmethod
    @override
    def sanitize_url(url: str) -> str:
        return url.replace("/ja/","/en/")

    @override
    def _parse_metadata(self) -> None:
        info_tag = self._soup.find("div", class_="deckSearch-deckList__information__flex")
        if not info_tag:
            raise ScrapingError("Info <div> tag not found", type(self), self.url)
        for ul_tag in info_tag.find_all("ul"):
            li_tags = ul_tag.find_all("li")
            if len(li_tags) != 2:
                continue
            cat_tag, value_tag = li_tags
            if cat_tag.text.strip() == "Deck Name":
                self._metadata["name"] = value_tag.text.strip()
            elif cat_tag.text.strip() == "Tournament":
                self._metadata["event"] = value_tag.text.strip()
            elif cat_tag.text.strip() == "Format":
                self._update_fmt(value_tag.text.strip())
            elif cat_tag.text.strip() == "Archetype":
                self._update_archetype_or_theme(value_tag.text.strip())
            elif cat_tag.text.strip() == "Player":
                self._metadata["author"] = value_tag.text.strip()
            elif cat_tag.text.strip() == "Score":
                self._metadata["event_score"] = value_tag.text.strip()
            elif cat_tag.text.strip() == "Date":
                self._metadata["date"] = dateutil.parser.parse(value_tag.text.strip()).date()

        if not self._metadata.get("name") and self._metadata.get("hareruya_archetype"):
            self._metadata["name"] = self._metadata["hareruya_archetype"]

    @staticmethod
    def parse_card_name(card_tag: Tag) -> str:
        return card_tag.text.strip().strip("《》")

    @override
    def _parse_deck(self) -> None:
        main_tag = self._soup.find("div", class_="deckSearch-deckList__deckList__wrapper")

        for sub_tag in main_tag.descendants:
            if isinstance(sub_tag, NavigableString):
                continue

            if class_ := sub_tag.attrs.get("class"):
                if "deckSearch-deckList__deckList__totalNumber" in class_:
                    if "Sideboard" in sub_tag.text:
                        self._state.shift_to_sideboard()
                    elif "Commander" in sub_tag.text:
                        self._state.shift_to_commander()
                    elif not self._state.is_maindeck:
                        self._state.shift_to_maindeck()
            else:
                name_tag = sub_tag.find("a", class_="popup_product")
                if not name_tag:
                    continue
                name = self.parse_card_name(name_tag)
                qty_tag = sub_tag.find("span")
                if not qty_tag:
                    continue
                quantity = int(qty_tag.text)
                cards = self.get_playset(self.find_card(name), quantity)
                if self._state.is_maindeck:
                    self._maindeck += cards
                elif self._state.is_sideboard:
                    self._sideboard += cards
                elif self._state.is_commander:
                    self._set_commander(cards[0])


class JapaneseHareruyaDeckJsonParser(JsonBasedDeckParser):
    """Parser of Japanese Hareruya deck JSON data.
    """
    @override
    def _parse_metadata(self) -> None:
        fmt = self._deck_data["format_name_en"]
        self._update_fmt(fmt)
        if name := self._deck_data.get("deck_name"):
            self._metadata["name"] = name
        if author := self._deck_data.get("nickname", self._deck_data.get("player_name", "")):
            self._metadata["author"] = author
        if arch := self._deck_data.get("archetype_name_en"):
            self._update_archetype_or_theme(arch)
        self._metadata["deck_type"] = self._deck_data["deck_type"]
        if event_name := self._deck_data.get("event_name_en"):
            self._metadata.setdefault("event", {})
            self._metadata["event"]["name"] = event_name
        if event_date := self._deck_data.get("event_date"):
            self._metadata.setdefault("event", {})
            self._metadata["event"]["date"] = dateutil.parser.parse(event_date).date()
        if event_player := self._deck_data.get("player_name"):
            self._metadata.setdefault("event", {})
            self._metadata["event"]["player"] = event_player
        if event_result := self._deck_data.get("result"):
            self._metadata.setdefault("event", {})
            self._metadata["event"]["result"] = event_result
        if event_ranking := self._deck_data.get("ranking"):
            self._metadata.setdefault("event", {})
            self._metadata["event"]["ranking"] = event_ranking
        if source_url := self._deck_data.get("source_url"):
            self._metadata["original_source"] = source_url
        self._metadata["date"] = dateutil.parser.parse(self._deck_data["update_date"]).date()

    def _process_card(self, json_card: Json) -> None:
        quantity = json_card["count"]
        name = json_card["name_en"]
        if json_card["board_id"] == 1:
            self._maindeck.extend(self.get_playset(self.find_card(name), quantity))
        elif json_card["board_id"] == 2:
            self._sideboard.extend(self.get_playset(self.find_card(name), quantity))
        elif json_card["board_id"] == 3:
            self._set_commander(self.find_card(name))

    @override
    def _parse_deck(self) -> None:
        for card in self._deck_data["cards"]:
            self._process_card(card)


@throttled_deck_scraper
@DeckScraper.registered
class JapaneseHareruyaDeckScraper(DeckScraper):
    """Scraper of Japanese Hareruya decklist page.
    """
    API_URL_TEMPLATE = "https://api.deck.hareruyamtg.com/api/deck/{}?display_token={}"  # override

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        url = url.lower()
        return ("hareruyamtg.com/decks/list/" in url or "hareruyamtg.com/decks/" in url
                or "deck.hareruyamtg.com/deck/" in url) and not any(
            t in url for t in ("/user/", "/tile/", "/search"))

    @staticmethod
    @override
    def sanitize_url(url: str) -> str:
        if "deck.hareruyamtg.com/deck/" in url:
            return url.replace("deck.hareruyamtg.com/deck/", "www.hareruyamtg.com/decks/")
        return url

    @override
    def _is_soft_404_error(self) -> bool:
        return "ページが存在しません" in self._soup.text and "このページは存在しないか、" in self._soup.text

    @override
    def _get_data_from_api(self) -> Json:
        display_token_values = get_query_values(self.url, "display_token")
        display_token = display_token_values[0] if display_token_values else ""
        segments = get_path_segments(self.url)
        if not segments:
            raise ScrapingError(f"Unable to parse path segments from URL", type(self), self.url)
        decklist_id = segments[-1]
        if not decklist_id or not all(ch.isdigit() for ch in decklist_id):
            raise ScrapingError(
                f"Decklist ID needs to be a number, got: {decklist_id!r}", type(self), self.url)
        return fetch_json(
            self.API_URL_TEMPLATE.format(decklist_id, display_token))

    @override
    def _get_sub_parser(self) -> JapaneseHareruyaDeckJsonParser:
        return JapaneseHareruyaDeckJsonParser(self._data, self._metadata)

    @override
    def _parse_metadata(self) -> None:
        pass

    @override
    def _parse_deck(self) -> None:
        pass


HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,"
              "image/png,image/svg+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Connection": "keep-alive",
    "Cookie": SECRETS["hareruya"]["cookie"],
    "DNT": "1",
    "Host": "www.hareruyamtg.com",
    "Priority": "u=0, i",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "cross-site",
    "Sec-GPC": "1",
    "TE": "trailers",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:131.0) Gecko/20100101 "
                  "Firefox/131.0",
}


@DeckUrlsContainerScraper.registered
class HareruyaEventScraper(DeckUrlsContainerScraper):
    """Scraper of Hareruya event decks search page.
    """
    CONTAINER_NAME = "Hareruya event"  # override
    HEADERS = HEADERS  # override
    DECK_SCRAPERS = InternationalHareruyaDeckScraper, JapaneseHareruyaDeckScraper  # override

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return all(t in url for t in {"hareruyamtg.com", "/deck", "/result?", "eventName="})

    @override
    def _collect(self) -> list[str]:
        return [a_tag.attrs["href"] for a_tag in self._soup.find_all(
            "a", class_="deckSearch-searchResult__itemWrapper")]


@DeckUrlsContainerScraper.registered
class HareruyaPlayerScraper(DeckUrlsContainerScraper):
    """Scraper of Hareruya player decks search page.
    """
    CONTAINER_NAME = "Hareruya player"  # override
    HEADERS = HEADERS  # override
    DECK_SCRAPERS = InternationalHareruyaDeckScraper, JapaneseHareruyaDeckScraper  # override

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        url = url.lower()
        return all(t in url for t in {"hareruyamtg.com", "/deck", "/result?", "player="})

    @override
    def _collect(self) -> list[str]:
        return [a_tag.attrs["href"] for a_tag in self._soup.find_all(
            "a", class_="deckSearch-searchResult__itemWrapper")]


class HareruyaArticleDeckTagParser(TagBasedDeckParser):
    """Parser of deck tags embedded in (some) Hareruya articles.
    """
    def _parse_author_and_name(self, info_tag: Tag) -> None:
        li_tags = [*info_tag.find_all("li")]
        author_tag, name_tag = None, None
        if len(li_tags) >= 2:
            author_tag, name_tag, *_ = li_tags
        elif len(li_tags) == 1:
            author_tag, name_tag = li_tags[0], None
        if author_tag:
            self._metadata["author"] = author_tag.text.strip()
        if name_tag:
            self._metadata["name"] = name_tag.text.strip().removeprefix("– ")

    def _derive_fmt(self) -> None:
        text = self._metadata.get("name", "") + self._metadata.get("event", "")
        if fmt := self.derive_format_from_text(text, use_japanese=True):
            self._update_fmt(fmt)
        else:
            text = self._metadata.get("article", {}).get("title", "")
            for t in self._metadata.get("article", {}).get("tags", []):
                text += t
            if fmt := self.derive_format_from_text(text, use_japanese=True):
                self._update_fmt(fmt)

    @override
    def _parse_metadata(self) -> None:
        if caption_tag := self._deck_tag.find("div", class_="deck_caption"):
            info_tags = [*caption_tag.find_all("ul", class_="deck_info")]
            if len(info_tags) >= 2:
                author_name_tag, event_tag, *_ = info_tags
                self._parse_author_and_name(author_name_tag)
                self._metadata["event"] = event_tag.text.strip()
            elif len(info_tags) == 1:
                self._parse_author_and_name(info_tags[0])
        self._derive_fmt()

    def _parse_decklist_from_link(self, a_tag: Tag) -> None:
        url = a_tag.attrs["href"]
        response = fetch(url)
        if not response:
            raise ParsingError(f"Request for decklist tag's URL: {url!r} returned 'None'")
        self._decklist = response.text

    def _parse_deck_from_card_tags(self) -> None:
        col_tags = self._deck_tag.select("div.MediaDeckListColumn")
        for col_tag in col_tags:
            qty, card, section = None, None, None
            for item in col_tag:
                if isinstance(item, NavigableString):
                    try:
                        qty = extract_int(item.text)
                    except ValueError:
                        continue
                elif isinstance(item, Tag) and item.name == "span" and item.has_attr("class"):
                    if item["class"] == ["cardLink"]:
                        name = InternationalHareruyaDeckScraper.parse_card_name(item)
                        self._maindeck += self.get_playset(self.find_card(name), qty)
                        qty, name = None, None
                    elif item["class"] == ["media_red"]:
                        qty = extract_int(item.text)
                    elif item["class"] == ["MediaDeckList_count"]:
                        card_count = extract_int(item.text)
                        if any(t in item.text for t in ("統率者", "コマンダー", "Commander")):
                            commander_cards = []
                            for i in range(card_count):
                                commander_cards.append(self._maindeck.pop())
                            for cc in commander_cards[::-1]:
                                self._set_commander(cc)
                        elif "Sideboard" in item.text or "サイドボード" in item.text:
                            sideboard_cards = []
                            for i in range(card_count):
                                sideboard_cards.append(self._maindeck.pop())
                            self._sideboard = sideboard_cards[::-1]
                # part of "media_red" case
                elif isinstance(item, Tag) and item.name == "a" and item.attrs.get(
                        "class") == ["popup_product"]:
                    name = InternationalHareruyaDeckScraper.parse_card_name(item)
                    self._maindeck += self.get_playset(self.find_card(name), qty)
                    qty, name = None, None

    @override
    def _parse_deck(self) -> None:
        decklist_tag = find_next_sibling_tag(self._deck_tag)
        if not decklist_tag:
            raise ParsingError("Decklist tag not found")
        # not so old pages (ca.2022) have a (English) text decklist under a link within the next
        # sibling tag
        if a_tag := decklist_tag.find("a", href=True):
            self._parse_decklist_from_link(a_tag)
        # older pages (ca. 2020) have a (Japanese) text decklist under a <textarea> tag within
        # one more next sibling tag
        else:
            decklist_tag = find_next_sibling_tag(decklist_tag)
            if not decklist_tag:
                raise ParsingError("Decklist tag not found")
            if textarea_tag := decklist_tag.find("textarea"):
                self._decklist = textarea_tag.text.strip().replace("\r\n", "\n")
            # even older pages (ca. 2019) have no text decklist anywhere and need dedicated
            # scraping from card tags
            else:
                self._parse_deck_from_card_tags()


def _get_article_metadata(article_soup: BeautifulSoup) -> Json:
    metadata = {"article": {}}
    time_tag = article_soup.find("time", datetime=True)
    metadata["article"]["date"] = dateutil.parser.parse(time_tag["datetime"]).date()
    title_tag = article_soup.find("h1", class_="article-content__head__info__title")
    metadata["article"]["title"] = title_tag.text.strip()
    author_tag = article_soup.find("p", class_="article-content__head__info__auth")
    metadata["article"]["author"] = author_tag.text.strip()
    cat_tags = article_soup.select("p.article-content__head__info__category")
    tag_tags = [
        t for t in article_soup.select('a[href*="/article/tag/"]')
        if t.attrs.get("rel") == ["tag"]]
    tags = DeckScraper.sanitize_metadata_deck_tags([t.text.strip() for t in [*cat_tags, *tag_tags]])
    if tags:
        metadata["article"]["tags"] = tags
    return metadata


@DeckUrlsContainerScraper.registered
class HareruyaArticleScraper(HybridContainerScraper):
    """Scraper of Hareruya article page.

    There are 4 types of article decklists pages' formats (ordered chronologically from newest to
    oldest):
    1. https://article.hareruyamtg.com/article/72794/?lang=en
        or https://article.hareruyamtg.com/article/84119/ (<deck-embedder> tags with deck ID
        data that enables API queries for JSON deck data)
    2. https://article.hareruyamtg.com/article/60476/?lang=en (text decklist links along deck tags)
    3. https://article.hareruyamtg.com/article/44666/ (text decklists in <textarea> tags along
        deck tags)
    4. https://article.hareruyamtg.com/article/21533/?lang=en (only deck tags without any
        means to scrape text decklist)
    """
    CONTAINER_NAME = "Hareruya article"  # override
    JSON_BASED_DECK_PARSER = JapaneseHareruyaDeckJsonParser  # override
    TAG_BASED_DECK_PARSER = HareruyaArticleDeckTagParser  # override

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        try:
            url = strip_url_query(url).lower()
            return (is_more_than_root_path(url, "article.hareruyamtg.com/article/")
                    and not any(t in url for t in ("/page/", "/author/", "/category/", "/coverage/"))
                    and not urllib.parse.urlsplit(url).fragment)
        except ValueError:
            return False

    def _parse_metadata(self) -> None:
        self.update_metadata(**_get_article_metadata(self._soup))

    @staticmethod
    def _collect_deck_data(article_tag: Tag) -> Json:
        deck_data = []
        for tag in article_tag.find_all("deck-embedder", deckid=True):
            deck_id, token = tag.attrs["deckid"], tag.attrs.get("token", "")
            data = fetch_json(
                JapaneseHareruyaDeckScraper.API_URL_TEMPLATE.format(deck_id, token))
            if data:
                deck_data.append(data)
        return deck_data

    @staticmethod
    def _collect_deck_tags(article_tag: Tag) -> list[Tag]:
        return [*article_tag.find_all("div", class_="MediaDeckList")]

    @override
    def _collect(self) -> Collected:
        article_tag = self._soup.find("article")
        if not article_tag:
            err = ScrapingError("Article tag not found", scraper=type(self), url=self.url)
            _log.warning(f"Scraping failed with: {err!r}")
            return [], [], [], []
        # only the newest pages have <deck-embedder> tags with deck IDs that facilitates JSON
        # based parsing with API queries
        deck_data = self._collect_deck_data(article_tag)
        deck_urls, container_urls = self._find_links_in_tags(article_tag)
        if deck_data:
            return [], [], deck_data, container_urls
        # older ones need a dedicated tag based parser
        deck_tags = self._collect_deck_tags(article_tag)
        if deck_tags:
            return [], deck_tags, [], container_urls
        return deck_urls, [], [], container_urls


@DeckScraper.registered
class HareruyaArticleDeckScraper(DeckScraper):
    """Scraper of Hareruya article page that points to an individual deck.
    """
    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        try:
            url = strip_url_query(url).lower()
            return (is_more_than_root_path(url, "article.hareruyamtg.com/article/")
                    and not any(t in url for t in ("/page/", "/author/", "/category/", "/coverage/"))
                    and urllib.parse.urlsplit(url).fragment)
        except ValueError:
            return False

    @override
    def _get_sub_parser(self) -> HareruyaArticleDeckTagParser:
        did = urllib.parse.urlsplit(self.url).fragment
        deck_tag = self._soup.find("div", class_="MediaDeckList",  id=did)
        if deck_tag is None:
            raise ScrapingError(
                f"Decklist tag designated by {did!r} ID not found", scraper=type(self),
                url=self.url)
        return HareruyaArticleDeckTagParser(deck_tag, self._metadata)

    @override
    def _parse_metadata(self) -> None:
        self.update_metadata(**_get_article_metadata(self._soup))

    @override
    def _parse_deck(self) -> None:
        pass


@HybridContainerScraper.registered
class HareruyaAuthorScraper(HybridContainerScraper):
    """Scraper of Hareruya author page.
    """
    CONTAINER_NAME = "Hareruya author"  # override
    CONTAINER_SCRAPERS = HareruyaArticleScraper,  # override
    CONTAINER_URL_PREFIX = "https://article.hareruyamtg.com"

    @staticmethod
    @override
    def is_valid_url(url: str) -> bool:
        return "article.hareruyamtg.com/article/author/" in url.lower()

    @override
    def _collect(self) -> tuple[list[str], list[Tag], list[Json], list[str]]:
        a_tags = self._soup.select("article > a")
        return [], [], [], [
            t["href"] for t in a_tags if t.has_attr("href") and t["href"].startswith("/article/")]
