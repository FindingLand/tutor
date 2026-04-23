"""
Google Sheets writer for authorization data.
Writes extracted authorization fields to the target Google Sheet,
mapping to the existing column structure used by Tutor Me.

Column mapping is based on the actual Tutor Me masterfile structure:
  Col 1  - UCI (student ID)
  Col 2  - Student (name)
  Col 6  - Category (service type)
  Col 8  - Authorization Comments (date + auth# + key details)
  Col 9  - Contract Service Date 1st Auth (start - end)
  Col 13 - Current Auth Expiration Date (MM/DD/YY)
  Col 15 - Hours Per Month (hours + date range)
  Col 18 - Authorization Status (Approved/Received)
  Col 19 - SC (case manager / service coordinator name)
  Col 24 - Areas of Support (subjects)
  Col 30 - Additional Notes
"""

import os
import logging
from datetime import datetime
from typing import List

import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


class SheetsWriter:
    # Columns we should NEVER write to (formulas, validations, manual-only fields)
    SKIP_KEYWORDS = {
        "validation",
        "standardized sc",
        "guardian name",
        "cl director",
        "spanish",
        "assessment",
        "upcoming renewal",
        "summer",
        "student status",
        "pending confirmation",
        "hard to contact",
        "requested schedule",
        "parent requested mode",
        "virtual tutor",
        "current virtual tutor",
        "in home tutor",
        "current in home tutor",
        "parent feedback",
        "start date track",
        "requested authorization",
        "2nd authorization",
        "3rd authorization",
        "4th authorization",
    }

    def __init__(self):
        creds_path = os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json")
        sheet_url = os.getenv("GOOGLE_SHEET_URL")

        if not sheet_url:
            raise ValueError("GOOGLE_SHEET_URL not set in .env file")

        if not os.path.exists(creds_path):
            raise FileNotFoundError(
                f"Google credentials file not found: {creds_path}\n"
                "Download it from Google Cloud Console and save as credentials.json"
            )

        # Authenticate
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
        self.gc = gspread.authorize(creds)

        # Open the sheet
        try:
            self.spreadsheet = self.gc.open_by_url(sheet_url)
            self.worksheet = self.spreadsheet.sheet1
            logger.info(f"Connected to sheet: {self.spreadsheet.title}")
        except gspread.exceptions.SpreadsheetNotFound:
            raise ValueError(
                "Cannot access the Google Sheet. Make sure you shared it with "
                "the service account email in your credentials.json"
            )

        # Read headers and build column index
        self.headers = self.worksheet.row_values(1)
        self.col_index = {h: i for i, h in enumerate(self.headers)}
        logger.info(f"Found {len(self.headers)} columns in sheet")

    def _should_skip(self, header: str) -> bool:
        """Check if a column should be skipped."""
        header_lower = header.lower()
        return any(skip in header_lower for skip in self.SKIP_KEYWORDS)

    def _format_auth_comment(self, result: dict) -> str:
        """Format authorization comment matching Tutor Me's manual entry style.

        Their style example (single line, concise):
          04/21/26: 99887766 - DIR F/F ONLY. MAX MONTHS: 4. GROSS AUTH AMT: $1,000.00.
        """
        today = datetime.now().strftime("%m/%d/%y")
        auth_num = result.get("authorization_number", "")
        notes = result.get("notes", "")

        # Match their concise single-line format: DATE: AUTH# - KEY NOTES.
        comment = f"{today}: {auth_num}"
        if notes:
            comment += f" - {notes}"

        return comment

    def _format_hours(self, result: dict) -> str:
        """Format hours per month matching their style.
        Their style: '30 hours/month (09/01/26 - 12/31/26)'
        """
        hours = result.get("authorized_hours_per_month", "")
        start = result.get("start_date", "")
        end = result.get("end_date", "")

        if not hours:
            return ""

        formatted = f"{hours} hours/month"
        if start and end:
            formatted += f" ({start} - {end})"
        return formatted

    def _format_service_dates(self, result: dict) -> str:
        """Format service dates for Contract Service Date column.
        Their style: '2/1/25 - 5/31/25'
        """
        start = result.get("start_date", "")
        end = result.get("end_date", "")
        if start and end:
            return f"{start} - {end}"
        return start or end or ""

    def _format_expiration_date(self, result: dict) -> str:
        """Format expiration date as M/DD/YY to match their column format."""
        end = result.get("end_date", "")
        if not end:
            return ""
        # The LLM now returns M/DD/YY format, pass through directly
        return end

    def _build_row(self, result: dict) -> dict:
        """Build a dict of {col_index: value} for only the cells we have data for.

        Returns a sparse dict instead of a full row, so we never overwrite
        dropdown defaults or formula columns with empty strings.
        """
        cells = {}

        for header, idx in self.col_index.items():
            if self._should_skip(header):
                continue

            header_lower = header.lower()

            # Col 1: UCI = student ID
            if header_lower == "uci":
                val = result.get("student_id", "")
                if val:
                    cells[idx] = val

            # Col 2: Student name (title case, PDFs often have ALL CAPS)
            elif header_lower == "student":
                name = result.get("student_name", "")
                if name:
                    cells[idx] = name.title()

            # Col 6: Category - dropdown, leave for manual selection
            elif header_lower == "category":
                pass

            # Col 8: Authorization Comments
            elif header_lower == "authorization comments":
                val = self._format_auth_comment(result)
                if val:
                    cells[idx] = val

            # Col 9: Contract Service Date 1st Auth
            elif "1st auth" in header_lower:
                val = self._format_service_dates(result)
                if val:
                    cells[idx] = val

            # Col 13: Current Auth Expiration Date
            elif "current auth expiration" in header_lower:
                val = self._format_expiration_date(result)
                if val:
                    cells[idx] = val

            # Col 15: Hours Per Month
            elif header_lower == "hours per month":
                val = self._format_hours(result)
                if val:
                    cells[idx] = val

            # Col 18: Authorization Status
            elif header_lower == "authorization status":
                cells[idx] = "Received"

            # Col 19: SC (First Name Last Name) = case manager
            elif header_lower.startswith("sc"):
                val = result.get("case_manager_name", "")
                if val:
                    cells[idx] = val

            # Col 24: Areas of Support = subject areas
            elif header_lower == "areas of support":
                val = result.get("subject_areas", "")
                if val:
                    cells[idx] = val

            # Col 30: Additional Notes
            elif header_lower == "additional notes":
                source = result.get("source_file", "")
                if source:
                    cells[idx] = f"Auto-extracted from: {source}"

        return cells

    def write_results(self, results: List[dict]):
        """Write extraction results to the Google Sheet.

        Only writes to cells where we have actual data, preserving
        dropdown defaults and formatting in columns we don't populate.
        """
        cell_dicts = [self._build_row(r) for r in results]

        # Find first row where UCI (col A) is empty
        all_values = self.worksheet.get_all_values()
        next_row = len(all_values) + 1  # fallback: after all rows

        for i, row_data in enumerate(all_values):
            if i == 0:
                continue  # skip header
            if not row_data[0].strip():
                next_row = i + 1  # 1-indexed
                break

        # Write only cells that have data (sparse update)
        if cell_dicts:
            cells_to_update = []
            for offset, cell_dict in enumerate(cell_dicts):
                row_num = next_row + offset
                for col_idx, value in cell_dict.items():
                    cell = gspread.Cell(row=row_num, col=col_idx + 1, value=value)
                    cells_to_update.append(cell)

            if cells_to_update:
                self.worksheet.update_cells(cells_to_update)
                logger.info(
                    f"Wrote {len(cells_to_update)} cell(s) across "
                    f"{len(cell_dicts)} row(s) starting at row {next_row}"
                )

        return next_row
