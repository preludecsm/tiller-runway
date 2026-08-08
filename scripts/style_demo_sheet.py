#!/usr/bin/env python3
"""
style_demo_sheet.py — apply Tiller-style visual formatting to the demo Google Sheet, so it reads
as a real Tiller workbook rather than a bare data dump. Run after generate_demo_data.py.

Colors are pulled from tiller.com's own site CSS (not guessed): a muted green (#6ea359) for
header rows and a light green tint (#f0f9f0) for row banding, both real Tiller brand colors, not
this project's invention.

The four tabs that are genuinely native to Tiller (Categories, Transactions, Balance History,
Accounts) get the full treatment: colored frozen header, row banding, currency/date number
formats, tuned column widths, and a filter view (Tiller's own convention -- a basic Filter on the
header row triggers a warning in real Tiller sheets, so real templates use Filter Views instead).
The two custom tabs (Downsize Model, Portfolio Model) get a deliberately plainer style -- bold
header, no green branding -- so the visual language itself reinforces what SETUP_GOOGLE_SHEETS.md
already says in words: these two aren't native Tiller structure.

  python3 style_demo_sheet.py
"""
import gspread, glob, os, sys
from google.oauth2.service_account import Credentials
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import PROJECT_ROOT, SHEET_ID

os.chdir(PROJECT_ROOT)

GREEN = {'red': 0x6e / 255, 'green': 0xa3 / 255, 'blue': 0x59 / 255}
GREEN_TINT = {'red': 0xf0 / 255, 'green': 0xf9 / 255, 'blue': 0xf0 / 255}
WHITE = {'red': 1, 'green': 1, 'blue': 1}
PLAIN_HEADER = {'red': 0xe8 / 255, 'green': 0xe8 / 255, 'blue': 0xe8 / 255}

NATIVE_TABS = ('Categories', 'Transactions', 'Balance History', 'Accounts')
CUSTOM_TABS = ('Downsize Model', 'Portfolio Model')

# per-tab column width tuning (header name -> pixel width); anything not listed gets a default
COLUMN_WIDTHS = {
    'Date': 90, 'Description': 220, 'Category': 140, 'Amount': 110, 'Account': 140, 'Tags': 90,
    'Group': 110, 'Type': 90, 'Bucket': 150, 'Tier': 90,
    'Balance': 110, 'Class': 90, 'Account Status': 120,
    'Institution': 130, 'On Balance Sheet': 140,
    'Assumption': 340, 'Value': 100, 'Note': 320,
}
CURRENCY_COLS = ('Amount', 'Balance', 'Value')
DATE_COLS = ('Date',)

def connect():
    creds = Credentials.from_service_account_file(glob.glob('./*.json')[0],
        scopes=['https://www.googleapis.com/auth/spreadsheets'])
    return gspread.authorize(creds).open_by_key(SHEET_ID)

def style_sheet(book, ws, native):
    hdr = ws.row_values(1)
    ncols = len(hdr)
    nrows = ws.row_count
    sheet_id = ws.id
    header_bg = GREEN if native else PLAIN_HEADER
    header_fg = WHITE if native else {'red': 0.1, 'green': 0.1, 'blue': 0.1}
    requests = []

    # freeze header row
    requests.append({'updateSheetProperties': {
        'properties': {'sheetId': sheet_id, 'gridProperties': {'frozenRowCount': 1}},
        'fields': 'gridProperties.frozenRowCount'}})

    # header row: bold, colored background/text, centered
    requests.append({'repeatCell': {
        'range': {'sheetId': sheet_id, 'startRowIndex': 0, 'endRowIndex': 1,
                  'startColumnIndex': 0, 'endColumnIndex': ncols},
        'cell': {'userEnteredFormat': {
            'backgroundColor': header_bg,
            'textFormat': {'bold': True, 'foregroundColor': header_fg},
            'horizontalAlignment': 'CENTER', 'verticalAlignment': 'MIDDLE'}},
        'fields': 'userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)'}})

    # column widths
    for i, name in enumerate(hdr):
        width = COLUMN_WIDTHS.get(name, 120)
        requests.append({'updateDimensionProperties': {
            'range': {'sheetId': sheet_id, 'dimension': 'COLUMNS', 'startIndex': i, 'endIndex': i + 1},
            'properties': {'pixelSize': width}, 'fields': 'pixelSize'}})

    # currency / date number formats, by header name
    for i, name in enumerate(hdr):
        if name in CURRENCY_COLS:
            requests.append({'repeatCell': {
                'range': {'sheetId': sheet_id, 'startRowIndex': 1, 'endRowIndex': nrows,
                          'startColumnIndex': i, 'endColumnIndex': i + 1},
                'cell': {'userEnteredFormat': {'numberFormat': {
                    'type': 'CURRENCY', 'pattern': '$#,##0.00;($#,##0.00)'}}},
                'fields': 'userEnteredFormat.numberFormat'}})
        elif name in DATE_COLS:
            requests.append({'repeatCell': {
                'range': {'sheetId': sheet_id, 'startRowIndex': 1, 'endRowIndex': nrows,
                          'startColumnIndex': i, 'endColumnIndex': i + 1},
                'cell': {'userEnteredFormat': {'numberFormat': {
                    'type': 'DATE', 'pattern': 'M/d/yyyy'}}},
                'fields': 'userEnteredFormat.numberFormat'}})

    if native:
        # row banding -- alternating white / light-green-tint, matching real Tiller's own tint
        requests.append({'addBanding': {'bandedRange': {
            'range': {'sheetId': sheet_id, 'startRowIndex': 0, 'endRowIndex': nrows,
                      'startColumnIndex': 0, 'endColumnIndex': ncols},
            'rowProperties': {
                'headerColor': header_bg,
                'firstBandColor': WHITE,
                'secondBandColor': GREEN_TINT}}}})

        # filter view -- Tiller's own convention over a plain Filter (which triggers a "heads up"
        # warning in real Tiller sheets if applied to the header row)
        requests.append({'addFilterView': {'filter': {
            'title': f'{ws.title} filter',
            'range': {'sheetId': sheet_id, 'startRowIndex': 0, 'endRowIndex': nrows,
                      'startColumnIndex': 0, 'endColumnIndex': ncols}}}})

    book.batch_update({'requests': requests})
    print(f'styled {ws.title} ({"native" if native else "custom"})')

def main():
    book = connect()
    for name in NATIVE_TABS:
        style_sheet(book, book.worksheet(name), native=True)
    for name in CUSTOM_TABS:
        style_sheet(book, book.worksheet(name), native=False)

if __name__ == '__main__':
    main()
