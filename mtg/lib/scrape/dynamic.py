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

import backoff
import pyperclip
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common import (
    ElementClickInterceptedException, NoSuchElementException,
    StaleElementReferenceException, TimeoutException, WebDriverException,
)
from selenium.webdriver import ActionChains, Keys
from selenium.webdriver.chrome.webdriver import WebDriver
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait

from mtg.constants import Json
from mtg.lib.time import timed

_log = logging.getLogger(__name__)
SELENIUM_TIMEOUT = 20.0  # seconds
SCROLL_DOWN_TIMES = 50


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


@timed("fetching dynamic soup")
@backoff.on_exception(
    backoff.expo,
    (ElementClickInterceptedException, StaleElementReferenceException, CloudflareInvalidSslError),
    max_time=300
)
def fetch_dynamic_soup(
        url: str,
        xpath: str,
        halt_xpaths: Iterable[str] = (),
        click=False,
        wait_for_all=False,
        consent_xpath="",
        wait_for_consent_disappearance=True,
        clipboard_xpath="",
        scroll_down=False,
        scroll_down_delay=0.0,
        scroll_down_times=SCROLL_DOWN_TIMES,
        headers: dict[str, str] | None = None,
        timeout=SELENIUM_TIMEOUT) -> tuple[BeautifulSoup, BeautifulSoup | None, str | None]:
    """Return BeautifulSoup object(s) from dynamically rendered page source at ``url`` using
    Selenium WebDriver that waits for presence of an element specified by ``xpath``.

    If specified, attempt at clicking the located element first is made and two soup objects are
    returned (with state before and after the click).

    If consent XPath is specified and points to a clickable consent element, then its
    presence first is checked and, if confirmed, consent is clicked before attempting any other
    action.

    If specified, a copy-to-clipboard element is clicked and the contents of the clipboard are
    returned as the third object.

    If specified, an attempt to scroll the whole page down is performed before anything other
    than optional consent clicking.

    Args:
        url: webpage's URL
        xpath: XPath to locate the main element
        halt_xpaths: XPaths to locate elements that should halt the wait
        click: if True, main element is clicked before returning the soups
        wait_for_all: if True, wait for presence of all elements located by ``xpath``
        consent_xpath: XPath to locate a consent button (if present)
        wait_for_consent_disappearance: if True, wait for the consent window to disappear
        clipboard_xpath: Xpath to locate a copy-to-clipboard button (if present)
        scroll_down: if True, scroll the page down before returning the soups
        scroll_down_delay: delay in seconds after scrolling to the bottom
        scroll_down_times: times the scroll down is performed (before going to the end)
        headers: optionally, request headers to inject
        timeout: timeout used in attempted actions (consent timeout is halved)

    Returns:
        tuple of: BeautifulSoup object from dynamically loaded page source, second such object (if
        the located element was clicked), clipboard content (if copy-to-clipboard element was
        clicked)
    """
    with webdriver.Chrome() as driver:
        _log.info(f"Webdriving using Chrome to: '{url}'...")

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
            if wait_for_consent_disappearance:
                accept_consent(driver, consent_xpath)
            else:
                accept_consent_without_wait(driver, consent_xpath)

        if scroll_down:
            time.sleep(1)
            scroll_down_by_offset(driver, times=scroll_down_times)
            scroll_down_with_end(driver, delay=scroll_down_delay)

        element = _wait_for_elements(
            driver, xpath, *halt_xpaths, wait_for_all=wait_for_all, timeout=timeout)

        verb = "are" if wait_for_all else "is"
        if not element:
            raise NoSuchElementException(
                f"Element(s) specified by {xpath!r} {verb} not present")
        _log.info(f"Page has been loaded and XPath-specified element(s) {verb} present")

        page_source, soup2 = driver.page_source, None
        if click:
            element = element[0] if isinstance(element, list) else element
            element.click()
            soup2 = BeautifulSoup(driver.page_source, "lxml")
        soup = BeautifulSoup(page_source, "lxml")

        clipboard = None
        if clipboard_xpath:
            clipboard = click_for_clipboard(driver, clipboard_xpath)

        return soup, soup2, clipboard


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


def accept_consent(driver: WebDriver, xpath: str, timeout=SELENIUM_TIMEOUT) -> None:
    """Accept consent by clicking element located by ``xpath`` with the passed Chrome
    webdriver.

    If the located element is not present, this function just returns doing nothing. Otherwise,
    the located element is clicked and the driver waits for its disappearance.

    Args:
        driver: a Chrome webdriver object
        xpath: XPath to locate the consent button to be clicked
        timeout: wait this much for appearance or disappearance of the located element
    """
    _log.info("Attempting to close consent pop-up (if present)...")
    # locate and click the consent button if present
    try:
        consent_button = WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable((By.XPATH, xpath)))
        consent_button.click()
        _log.info("Consent button clicked")
    except TimeoutException:
        _log.info("No need for accepting. Consent window not found")
        return None

    # wait for the consent window to disappear
    try:
        WebDriverWait(driver, timeout).until_not(
            EC.presence_of_element_located((By.XPATH, xpath)))
        _log.info("Consent pop-up closed")
    except TimeoutException:
        driver.quit()
        raise


def accept_consent_without_wait(
        driver: WebDriver, xpath: str, timeout=SELENIUM_TIMEOUT) -> None:
    """Accept consent by clicking element located by ``xpath`` with the passed Chrome
    webdriver. Don't wait for the consent window to disappear.

    If the located element is not present, this function just returns doing nothing. Otherwise,
    the located element is clicked and the function returns without waiting.

    Args:
        driver: a Chrome webdriver object
        xpath: XPath to locate the consent button to be clicked
        timeout: wait this much for appearance of the located element
    """
    _log.info("Attempting to close consent pop-up (if present)...")
    # locate and click the consent button if present
    try:
        consent_button = WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable((By.XPATH, xpath)))
        consent_button.click()
        _log.info("Consent button clicked")
    except TimeoutException:
        _log.info("No need for accepting. Consent window not found")
        return None


def click_for_clipboard(
        driver: WebDriver, xpath: str, delay=0.5, timeout=SELENIUM_TIMEOUT / 2) -> str:
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

    try:
        copy_element = WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable((By.XPATH, xpath)))
        copy_element.click()
        _log.info(f"Copy-to-clipboard element clicked")
        time.sleep(delay)
        return pyperclip.paste()

    except TimeoutException:
        driver.quit()
        raise


def _wait_for_elements(
        driver: WebDriver, xpath: str, *halt_xpaths: str, wait_for_all=False,
        timeout=SELENIUM_TIMEOUT) -> WebElement | list[WebElement] | None:
    """Wait for elements specified by ``xpath`` and ``halt_xpaths`` to be present in the current
    page.

    If ``xpath`` element is located return it. If any element designated by``halt_xpaths`` is
    located return `None`.

    Args:
        driver: a Chrome webdriver object
        xpath: XPath to locate the main element
        halt_xpaths: XPaths to locate elements that should halt the wait
        wait_for_all: wait for all elements to be present or just one
        timeout: timeout used in attempted actions
    """
    if halt_xpaths:
        WebDriverWait(driver, timeout).until(
        EC.any_of(
            EC.presence_of_element_located((By.XPATH, xpath)),
            *[EC.presence_of_element_located((By.XPATH, xp)) for xp in halt_xpaths]
        ))

        # check which element was found
        elements = driver.find_elements(By.XPATH, xpath)
        if elements:
            return elements[0]

        _log.warning("Halting element found")
        return None

    if wait_for_all:
        return WebDriverWait(driver, timeout).until(
            EC.presence_of_all_elements_located((By.XPATH, xpath)))

    return WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((By.XPATH, xpath)))


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


def scroll_down_by_offset(
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


def scroll_down_with_end(driver, element: WebElement | None = None, delay=0.0) -> None:
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
