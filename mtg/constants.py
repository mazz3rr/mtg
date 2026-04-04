"""

    mtg.constants
    ~~~~~~~~~~~~~
    App's hardcoded values.

    @author: mazz3rr

"""
import json
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Union

# type aliases
type Json = Union[str, int, float, bool, datetime, date, None, Dict[str, "Json"], List["Json"]]
type PathLike = str | Path

APP_DIR = Path.home() / f".mtg_decks"
APP_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR = APP_DIR / "mtg_data"
SECRETS = json.loads((APP_DIR / "secrets.json").read_text(encoding="utf-8"))
ROOT_DIR = Path(__file__).parent.parent
VAR_DIR = ROOT_DIR / "var"
OUTPUT_DIR = VAR_DIR / "output"
DECKS_DIR = OUTPUT_DIR / "decks"
WITHDRAWN_DIR = OUTPUT_DIR / "withdrawn"
README_FILE = ROOT_DIR / "README.md"
GOOGLE_API_KEY = SECRETS["google"]["api_key"]  # not used anywhere
FILENAME_TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"
READABLE_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
SECONDS_IN_YEAR = 365.25 * 24 * 60 * 60  # with leap years

CHANNELS_DIR = OUTPUT_DIR / "channels"
DECKLISTS_FILE = CHANNELS_DIR / "decklists.json"
DECKLISTS_WITH_PRINTINGS_FILE = CHANNELS_DIR / "decklists_with_printings.json"
FAILED_URLS_FILE = CHANNELS_DIR / "failed_urls.json"
