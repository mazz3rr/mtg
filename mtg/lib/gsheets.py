"""

    mtg.lib.gsheets
    ~~~~~~~~~~~~~~~
    Google Sheets as backend/frontend.

    @author: mazz3rr

"""
import logging

import gspread
import gspread.utils

from mtg.lib.time import timed

_log = logging.getLogger(__name__)


def _get_worksheet(spreadsheet: str, worksheet: str) -> gspread.Worksheet:
    creds_file = "scraping_service_account.json"
    client = gspread.service_account(filename=creds_file)
    spreadsheet = client.open(spreadsheet)
    worksheet = spreadsheet.worksheet(worksheet)
    return worksheet


@timed("retrieving from Google Sheets")
def retrieve_from_gsheets_cols(
        spreadsheet: str, worksheet: str, cols=(1, ), start_row=1,
        ignore_none=True) -> tuple[list[str], ...]:
    """Retrieve a list of string values from a Google Sheets worksheet for each specified column.
    """
    if any(col < 1 for col in cols) or start_row < 1:
        raise ValueError("A column and a start row must be positive integers")
    _log.info(
        f"Retrieving records from '{spreadsheet}/{worksheet}' Google sheet (columns {cols})...")
    worksheet = _get_worksheet(spreadsheet, worksheet)
    columns = []
    for col in cols:
        values = worksheet.col_values(col, value_render_option="UNFORMATTED_VALUE")[start_row-1:]
        if ignore_none:
            values = [value for value in values if value is not None]
        columns.append(values)
    return tuple(columns)


@timed("saving to Google Sheets")
def insert_gsheets_rows(
        spreadsheet: str, worksheet: str, rows: list[list[str]], start_row=1) -> None:
    """Save a list of strings to a Google Sheets worksheet's specified column.
    """
    if start_row < 1:
        raise ValueError("Start row must be a positive integer")
    _log.info(
        f"Inserting {len(rows)} row(s) into '{spreadsheet}/{worksheet}' Google sheet...")
    worksheet = _get_worksheet(spreadsheet, worksheet)
    worksheet.insert_rows(rows, row=start_row)


@timed("saving to Google Sheets")
def extend_gsheet_rows_with_cols(
        spreadsheet: str, worksheet: str, rows: list[list[str]], start_row=1,
        start_col: int | None = None) -> None:
    """Extend a Google Sheets worksheet with columns according to passed row data.
    """
    if not rows:
        _log.warning("Nothing provided to update")
        return
    if start_row < 1:
        raise ValueError("Start row must be a positive integer")
    if start_col is not None and start_col < 1:
        raise ValueError("Start column must be a positive integer")
    worksheet_obj = _get_worksheet(spreadsheet, worksheet)
    last_col = len(worksheet_obj.row_values(1)) if start_col is None else start_col - 1
    start_cell = gspread.utils.rowcol_to_a1(start_row, last_col + 1)
    end_cell = gspread.utils.rowcol_to_a1(start_row + len(rows) - 1, last_col + len(rows[0]))
    range_notation = f"{start_cell}:{end_cell}"
    _log.info(f"Updating {range_notation!r} range in '{spreadsheet}/{worksheet}' Google sheet...")
    worksheet_obj.update(range_notation, rows)
