"""

    mtg.lib.scrape.dynamic
    ~~~~~~~~~~~~~~~~~~~~~~
    Utilities for scraping dynamic sites.

    @author: mazz3rr

"""
import json
import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import NamedTuple

import backoff
import pyperclip
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common import (
    ElementClickInterceptedException, NoSuchElementException,
    StaleElementReferenceException, TimeoutException, WebDriverException,
)
from selenium.webdriver import ActionChains, Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.webdriver import WebDriver
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait

from mtg.constants import Json
from mtg.lib.time import timed

_log = logging.getLogger(__name__)
DEFAULT_TIMEOUT = 20.0  # seconds
CONSENT_TIMEOUT = DEFAULT_TIMEOUT / 2
CLIPBOOARD_TIMEOUT = DEFAULT_TIMEOUT / 3
SCROLL_DOWN_DELAY = 0.0
SCROLL_DOWN_TIMES = 50


@dataclass
class Xpath:
    """XPath expression with a bit of logic attached for Selenium to better process waiting for
    elements.
    """
    text: str
    wait_for_all: bool = False
    halt_xpaths: tuple[str, ...] = ()
    timeout: float | None = None


class _WaitResult(NamedTuple):
    locator: Xpath
    elements: list[WebElement]
    was_halted: bool = False
    timeout_exc: TimeoutException | None = None


class ScrollDown(NamedTuple):
    times: int = SCROLL_DOWN_TIMES
    delay: float = SCROLL_DOWN_DELAY


@dataclass
class ConsentXpath:
    text: str
    wait_for_disappearance: bool = False
    timeout: float | None = None


class CloudflareInvalidSslError(WebDriverException):
    """Raised as a sentinel for backoff on target's infrastructure spawning a Cloudflare 526 error.

    More on reasons for this spawning: https://share.google/aimode/1B551gxjT4039uigg
    """


class CloudflareBlockError(WebDriverException):
    """Raised on a Cloudflare block being detected to short-circuit scraping that is anyway
    doomed to fall.
    """


def _is_invalid_ssl_error(driver: WebDriver) -> bool:
    tokens = "526", "invalid", "ssl"
    if any(t in driver.title.lower() for t in tokens):
        return True
    return False


# pretty conservative for now
def _is_cloudflare_block(soup: BeautifulSoup) -> bool:
    if _ := soup.find("p", string=lambda s: "malicious bots" in s.strip().lower()):
        return True
    return False


def accept_consent(driver: WebDriver, xpath: ConsentXpath, timeout=CONSENT_TIMEOUT) -> None:
    """Accept consent by clicking element located by ``xpath`` with the passed Chrome
    webdriver.

    If the located element is not present, this function just returns doing nothing. Otherwise,
    the located element is clicked and the driver waits (or not) for its disappearance.

    Args:
        driver: a Chrome webdriver object
        xpath: ConsentXpath object to locate the consent button to be clicked
        timeout: wait this much for appearance or disappearance of the located element
    """
    _log.info("Attempting to close consent pop-up (if present)...")
    # locate and click the consent button if present
    try:
        consent_button = WebDriverWait(driver, xpath.timeout or timeout).until(
            EC.element_to_be_clickable((By.XPATH, xpath.text)))
        consent_button.click()
        _log.info("Consent button clicked")
    except TimeoutException:
        _log.info("No need for accepting. Consent window not found")
        return

    if xpath.wait_for_disappearance:
        WebDriverWait(driver, xpath.timeout or timeout).until_not(
            EC.presence_of_element_located((By.XPATH, xpath.text)))
        _log.info("Consent pop-up closed")


def click_for_clipboard(
        driver: WebDriver, xpath: str, delay=0.5, timeout=CLIPBOOARD_TIMEOUT) -> str:
    """Click element located by ``xpath`` with the passed Chrome webdriver and return clipboard
    contents.

    This function assumes that clicking the located element causes an OS clipboard to be populated.

    If consent XPath is specified (it should point to a clickable consent button), then its
    presence first is checked and, if confirmed, consent is clicked before attempting any other
    action.

    Args:
        driver: a Chrome webdriver object
        xpath: XPath to locate the main element
        delay: delay in seconds to wait for clipboard to be populated
        timeout: timeout used in attempted actions

    Returns:
        string clipboard content
    """
    _log.info("Attempting to click an element to populate clipboard...")
    copy_element = WebDriverWait(driver, timeout).until(
        EC.element_to_be_clickable((By.XPATH, xpath)))
    copy_element.click()
    _log.info(f"Copy-to-clipboard element clicked")
    time.sleep(delay)
    return pyperclip.paste()


def _wait_for_elements(
        driver: WebDriver, xpaths: Iterable[Xpath], timeout=DEFAULT_TIMEOUT) -> list[_WaitResult]:
    """Wait for elements specified by Xpath objects in a way defined by them.

    This functions allows for multiple Xpaths queries to expressed to consecutively wait for
    elements. In each case it can be a full wait for all elements, optionally short-circuited by
    presence of other elements.

    This approach makes possible processing problematic sites like Flexslot user page where:
        1. the three scraped component data buckets of: decks, sideboard guides and articles may
            or may not be present
        2. the needed elements don't show up in some container element that can be waited for
            instead of them, and as such require full wait (until all of them show up)

    Args:
        driver: a Chrome webdriver object
        xpaths: XPaths to locate elements and wait for them to be present
        timeout: timeout used in attempted actions
    """
    if not xpaths:
        raise ValueError("No XPaths to locate elements specified")

    results = []
    for xpath in xpaths:

        try:
            if xpath.wait_for_all:
                func = EC.presence_of_all_elements_located
            else:
                func = EC.presence_of_element_located

            if xpath.halt_xpaths:
                WebDriverWait(driver, xpath.timeout or timeout).until(
                EC.any_of(
                    func((By.XPATH, xpath.text)),
                    *[
                        EC.presence_of_element_located((By.XPATH, halt_xpath))
                        for halt_xpath in xpath.halt_xpaths
                    ]
                ))

                # check which element was found
                elements = driver.find_elements(By.XPATH, xpath.text)
                if elements:
                    results.append(_WaitResult(xpath, elements))
                    continue

                _log.warning(f"Halting element found (while waiting for {xpath.text!r})")
                results.append(_WaitResult(xpath, [], True))
                continue

            value = WebDriverWait(driver, timeout).until(func((By.XPATH, xpath.text)))
            results.append(_WaitResult(xpath, value if isinstance(value, list) else [value]))

        except TimeoutException as te:
            results.append(_WaitResult(xpath, [], timeout_exc=te))

    return results


def _scroll_down_by_offset(
        driver, pixel_offset=500, times=SCROLL_DOWN_TIMES, delay=0.3) -> None:
    """Scroll down to the element specified and down by the specified offset and number of times.

    Args:
        driver: a Chrome webdriver object
        pixel_offset: number of pixels to scroll
        times: number of times to scroll
        delay: wait time after each scroll in seconds
    """
    for _ in range(times):
        driver.execute_script(f"window.scrollBy(0, {pixel_offset});")
        time.sleep(delay)  # small wait between scrolls


def _scroll_down_with_end(driver, element: WebElement | None = None, delay=0.0) -> None:
    """Scroll down to the element specified and all the way down using END key.

    Args:
        driver: a Chrome webdriver object
        element: element to scroll to (<body> if not specified)
        delay: wait time after the scroll in seconds
    """
    element = element or driver.find_element(By.TAG_NAME, "body")
    element.send_keys(Keys.END)
    if delay:
        time.sleep(delay)


@timed("fetching dynamic soup")
@backoff.on_exception(
    backoff.expo,
    (ElementClickInterceptedException, StaleElementReferenceException, CloudflareInvalidSslError),
    max_time=300
)
def fetch_dynamic_soup(
    url: str,
    xpaths: Iterable[Xpath],
    consent_xpath: ConsentXpath | None = None,
    scroll_down: ScrollDown | None = None,
    click=False,
    clipboard_xpath="",
    headless=False,
    headers: dict[str, str] | None = None,
    timeout=DEFAULT_TIMEOUT,
) -> tuple[BeautifulSoup, BeautifulSoup | None, str | None]:
    """Return BeautifulSoup object(s) from dynamically rendered page source at the URL using
    Selenium WebDriver that waits for presence of an element(s) specified by XPath(s).

    If consent XPath is specified and points to a clickable consent element, then its
    presence first is checked and, if confirmed, consent is clicked before attempting any other
    action.

    If specified, an attempt to scroll the whole page down is performed before anything other
    than optional consent clicking.

    If specified, attempt at clicking the located element first is made and two soup objects are
    returned (with state before and after the click).

    If specified, a copy-to-clipboard element is clicked and the contents of the clipboard are
    returned as the third object.

    Args:
        url: webpage's URL
        xpaths: XPaths to locate elements and wait for them
        consent_xpath: XPath to locate a consent button (if present)
        click: if True, main element is clicked before returning the soups
        clipboard_xpath: Xpath to locate a copy-to-clipboard button (if present)
        scroll_down: scroll the page down as specified before returning the soups
        headless: if True, run Chrome in headless mode
        headers: optionally, request headers to inject
        timeout: timeout used in attempted actions (consent timeout is halved)

    Returns:
        tuple of: BeautifulSoup object, BeautifulSoup object after clicking or None, clipboard content (if copy-to-clipboard element was clicked)
    """
    if not xpaths:
        raise ValueError("No XPath to locate elements specified")


    options = Options()
    if headless:
        options.add_argument("--headless=new")

    with webdriver.Chrome(options=options) as driver:
        _log.info(f"Webdriving using {'headless ' if headless else ''}Chrome to: '{url}'...")

        if headers:
            driver.execute_cdp_cmd(
                'Network.setExtraHTTPHeaders',
                {'headers': headers}
            )
            driver.execute_cdp_cmd("Network.enable", {})

        driver.get(url)

        if _is_invalid_ssl_error(driver):
            raise CloudflareInvalidSslError("Cloudflare Error 526 encountered")

        if _is_cloudflare_block(BeautifulSoup(driver.page_source, "lxml")):
            raise CloudflareBlockError("Blocking by Cloudflare detected")

        if consent_xpath:
            accept_consent(driver, consent_xpath)

        if scroll_down:
            time.sleep(1)
            _scroll_down_by_offset(driver, times=scroll_down.times)
            _scroll_down_with_end(driver, delay=scroll_down.delay)

        results = _wait_for_elements(driver, xpaths, timeout=timeout)
        if all(not r.elements for r in results):
            if all(r.timeout_exc is not None for r in results):
                if len(results) == 1:
                    raise results[0].timeout_exc
                raise TimeoutException("Waiting for elements timed out")
            # halted by sentinel elements present and not timed out
            raise NoSuchElementException("Element(s) specified not present")

        _log.info(f"Page has been loaded and element(s) located")

        soup = BeautifulSoup(driver.page_source, "lxml")
        soup_after_click = None

        if click and len(results) == 1:
            # at least one result having at least one element is guaranteed at this point
            element = results[0].elements[0]
            element.click()
            soup_after_click = BeautifulSoup(driver.page_source, "lxml")

        clipboard = None
        if clipboard_xpath:
            clipboard = click_for_clipboard(driver, clipboard_xpath)

        return soup, soup_after_click, clipboard


@timed("fetching JSON with Selenium")
@backoff.on_exception(backoff.expo, json.decoder.JSONDecodeError, max_time=60)
def fetch_selenium_json(url: str) -> Json:
    """Fetch JSON data at ``url`` using Selenium WebDriver.

    This function assumes there's really JSON string at the destination and uses backoff
    redundancy on any problems with JSON parsing, so it'd better be.
    """
    with webdriver.Chrome() as driver:
        _log.info(f"Webdriving using Chrome to: '{url}'...")
        driver.get(url)
        soup = BeautifulSoup(driver.page_source, "lxml")
        return json.loads(soup.text)


# not used
def scroll_down(
        driver: WebDriver, element: WebElement | None = None, pixel_offset=0,
        delay=0.0) -> None:
    """Scroll down to the element specified or by the offset specified or to the bottom of the page.

    Args:
        driver: a Chrome webdriver object
        element: element to scroll to
        pixel_offset: number of pixels to scroll
        delay: wait time after the scroll in seconds
    """
    if element:
        driver.execute_script("arguments[0].scrollIntoView(true);", element)
    elif pixel_offset:
        driver.execute_script(f"window.scrollBy(0, {pixel_offset});")
    else:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    if delay:
        time.sleep(delay)  # small wait between scrolls


def scroll_down_with_mouse_wheel(
        driver: WebDriver, pixel_offset: int, element: WebElement | None = None, delay=0.0) -> None:
    """Scroll down to the element specified and down by the specified number of pixels using
    mouse wheel.

    Args:
        driver: a Chrome webdriver object
        pixel_offset: number of pixels to scroll
        element: element to scroll to (<body> if not specified)
        delay: wait time after the scroll in seconds
    """
    element = element or driver.find_element(By.TAG_NAME, "body")
    action = ActionChains(driver)
    action.move_to_element(
        element).click_and_hold().move_by_offset(0, pixel_offset).release().perform()
    if delay:
        time.sleep(delay)


def scroll_down_with_arrows(
        driver, times=SCROLL_DOWN_TIMES, element: WebElement | None = None, delay=0.1) -> None:
    """Scroll down to the element specified and down by the specified number of times using
    DOWN arrow key.

    Args:
        driver: a Chrome webdriver object
        times: number of times to scroll
        element: element to scroll to (<body> if not specified)
        delay: wait time after each scroll in seconds
    """
    element = element or driver.find_element(By.TAG_NAME, "body")
    for _ in range(times):
        element.send_keys(Keys.ARROW_DOWN)
        time.sleep(delay)  # small wait between scrolls
