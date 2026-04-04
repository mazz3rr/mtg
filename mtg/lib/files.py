"""

    mtg.lib.files
    ~~~~~~~~~~~~~
    Files-related utilities.

    @author: mazz3rr

"""
import os
import re
import shutil
from logging import getLogger
from pathlib import Path
from time import sleep

import requests
from tqdm import tqdm

from mtg.constants import PathLike

_log = getLogger(__name__)


def getdir(path: PathLike, create_missing=True) -> Path:
    """Return a directory path at ``path``.

    Optionally, create the directory (and all its needed parents) if it's missing.
    """
    dir_ = Path(path)
    if dir_.is_file():
        raise NotADirectoryError(f"Not a directory: '{dir_.resolve()}'")
    if not dir_.exists() and create_missing:
        _log.warning(f"Creating missing directory at: '{dir_.resolve()}'...")
        dir_.mkdir(parents=True, exist_ok=True)
    elif not dir_.exists():
        raise NotADirectoryError(f"Directory does not exist at: '{dir_.resolve()}'")
    return dir_


def getfile(path: PathLike, *extensions: str, suppress_errors=False) -> Path | None:
    """Return a path to existing file at ``path``.
    """
    f = Path(path)
    if not f.is_file():
        if suppress_errors:
            return None
        raise FileNotFoundError(f"Not a file: '{f.resolve()}'")
    if extensions and not f.suffix.lower() in {ext.lower() for ext in extensions}:
        if suppress_errors:
            return None
        raise ValueError(f"Not a {extensions} file")
    return f


def recursive_removedir(dirpath: str, check_delay: int = 500) -> None:
    """Remove directory at ``dirpath`` and it contents recursively. Check after delay (default is
    500ms), if something still exists, list it.
    """
    dir_ = getdir(dirpath, create_missing=False)
    if dir_ is not None:
        shutil.rmtree(dir_, ignore_errors=True)
        sleep(check_delay / 1000)
        if dir_.exists():
            _log.warning(
                f"Problems encountered while trying to remove: {dir_}. Content which hasn't been "
                f"removed: {os.listdir(dir_)}")
        else:
            _log.info(f"Removed successfully: {dir_} and its contents.")
    else:
        _log.info(f"Nothing to remove at {dirpath}.")


def remove_by_ext(ext: str, destdir: str, recursive=False, opposite=False) -> int:
    """Remove from ``destdir`` files by provided extension. Optionally, remove all files of
    different extension.

    Extension shall include the leading period, e.g. ".py"

    Returns:
        number of removed files
    """
    def remove(f: Path, removed_lst: list[Path]) -> None:
        f.unlink()
        if not f.exists():
            removed_lst.append(f)
            _log.info(f"Removed {f}.")
        else:
            _log.warning(f"Unable to remove file: {f}.")

    destdir = getdir(destdir)
    removed = []
    gb = "**/*" if recursive else "*"
    files = [f for f in destdir.glob(gb) if f.is_file()]
    for file in files:
        if opposite:
            if file.suffix != ext:
                remove(file, removed)
        else:
            if file.suffix == ext:
                remove(file, removed)

    return len(removed)


def download_file(url: str, file_name="", dst_dir="") -> None:
    """Download a file at ``url`` to destination specified by ``file_name`` and ``dst_dir``.

    Mostly, as suggested by GPT3.

    Args:
        url: URL of the file to be downloaded.
        file_name: Optional name for saved file. Default is the downloaded file's name.
        dst_dir: Optional destination directory for saving. Default is the CWD.
    """
    if not file_name:
        file_name = Path(url).name
    # send an HTTP request to the URL
    response = requests.get(url, stream=True)
    # get the total file size in bytes
    file_size = int(response.headers.get("Content-Length", 0))
    divisor = 1024

    dst = Path(file_name) if not dst_dir else getdir(dst_dir) / file_name
    # create a progress bar object
    progress = tqdm(response.iter_content(divisor), f"Downloading '{dst.resolve()}'...",
                    total=file_size, unit="B", unit_scale=True, unit_divisor=divisor)

    # open a file for writing
    with open(dst, "wb") as f:
        # iterate over the file content in chunks
        for chunk in progress:
            # write each chunk to the file
            f.write(chunk)
            # update the progress bar manually
            progress.update(len(chunk))


def sanitize_filename(text: str, replacement="_", remove_illegal=True) -> str:  # perplexity
    """Sanitize a string to make it suitable for use as a filename.

    Args:
        text: the string to be sanitized.
        replacement: the character to replace whitespace (and, optionally, illegal characters) with (default is underscore)
        remove_illegal: whether to remove illegal characters from the string (default is True)

    Returns:
        a sanitized string suitable for a filename.
    """
    # remove leading and trailing whitespace
    sanitized = text.strip()

    # replace illegal characters with the replacement character
    sanitized = re.sub(r'[<>:"/\\|?*]', "" if remove_illegal else replacement, sanitized)

    # replace any sequence of whitespace with a single underscore
    sanitized = re.sub(r'\s+', replacement, sanitized)

    # ensure the filename is not too long (most file systems have a limit of 255 characters)
    max_length = 255
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length]

    # ensure the filename does not end with a dot or space
    sanitized = sanitized.rstrip('. ')

    return sanitized


def truncate_path(path_str: str, max_bytes=4096, min_file_stem_length=5) -> str:
    """Truncates a path string to fit within the specified byte limit while preserving the
    folders' part.

    Args:
        path_str: the full path string to truncate
        max_bytes: maximum allowed bytes for the path
        min_file_stem_length: minimum length of the file stem to preserve

    Raises:
        ValueError: if the path is too long even after truncating the filename

    Returns:
        truncated path string
    """
    # convert to Path object for easier manipulation
    path = Path(path_str).resolve()
    path_str = str(path)

    # if path is already short enough, return original
    if len(path_str.encode()) <= max_bytes:
        return path_str

    overhead, stem = len(path_str.encode()) - max_bytes, path.stem
    while overhead >= 0 or len(stem) > min_file_stem_length:
        stem = stem[:-1]
        path = path.parent / f"{stem}{path.suffix}"
        overhead = len(str(path).encode()) - max_bytes

    if overhead:
        raise ValueError(f"Path '{path}' is still too long and cannot be further truncated")

    return str(path)
