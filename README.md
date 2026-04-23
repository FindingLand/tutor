# Authorization Document Reader

Extracts structured data from school district authorization PDFs using Claude (Anthropic) and writes results to a Google Sheet -- matching the existing Tutor Me masterfile column structure.

## How It Works

```
PDF file --> pdfplumber (text + tables) --> Claude Sonnet API (structured JSON) --> Google Sheet
```

1. **PDF Text Extraction**: Uses `pdfplumber` to extract both free text and table data from each page. Tables are parsed separately to preserve structured fields like service dates and authorized hours.

2. **LLM Structured Extraction**: Sends the combined text to Claude Sonnet with a prompt that returns JSON. The prompt handles the reality that different districts format authorizations completely differently -- field names vary, layouts vary, some are clean and some aren't. Each extraction includes warnings for fields that were inferred or ambiguous.

3. **Google Sheets Output**: Maps extracted fields to the existing masterfile columns (UCI, Student, Authorization Comments, Contract Service Dates, Hours Per Month, etc.). Skips formula/validation columns. Formats data to match the team's manual entry style.

## Fields Extracted

| Field | Description | Example |
|-------|-------------|---------|
| student_name | Student/client name | Alex Rivera |
| student_id | UCI / client ID | 1122334 |
| district | Issuing organization | Math Education Center |
| service_type | Service category | Academic Coaching |
| authorized_hours_per_month | Monthly hours (numeric) | 30 |
| start_date | Service start | 09/01/2026 |
| end_date | Service end | 12/31/2026 |
| authorization_number | Auth reference number | 99887766 |
| case_manager_name | Caseworker/coordinator | Jordan Smith |
| subject_areas | Academic subjects | Math |
| notes | Billing notes, conditions | F/F only, Title 17 rates |

Missing fields are logged as warnings -- the tool never crashes on missing data.

## Setup

### Prerequisites
- Python 3.9+
- Anthropic API key ([console.anthropic.com](https://console.anthropic.com/))
- Google Cloud service account with Sheets + Drive API enabled

### Installation

```bash
git clone https://github.com/YOUR_USERNAME/auth-document-reader.git
cd auth-document-reader
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Configuration

1. Copy `.env.example` to `.env` and fill in your values:

```
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_CREDENTIALS_PATH=credentials.json
GOOGLE_SHEET_URL=https://docs.google.com/spreadsheets/d/your-sheet-id/edit
```

2. Place your Google service account JSON key as `credentials.json` in the project root.

3. Share the target Google Sheet with the service account email (Editor access).

## Usage

```bash
# Process all PDFs in sample_pdfs/ and write to Google Sheet
python extract.py

# Process a single PDF
python extract.py --pdf path/to/authorization.pdf

# Dry run -- extract and print results, don't write to Sheets
python extract.py --dry-run

# Process from a different directory
python extract.py --pdf-dir /path/to/pdfs/
```

## Project Structure

```
auth-document-reader/
  extract.py          # Main pipeline: PDF -> LLM -> structured data
  sheets_writer.py    # Google Sheets integration with column mapping
  requirements.txt    # Python dependencies
  .env.example        # Environment variable template
  .gitignore          # Excludes credentials and cache files
  sample_pdfs/        # Place authorization PDFs here
  README.md
```

## Edge Cases Handled

- **Missing fields**: Logged as warnings, written as empty cells
- **Date format variations**: Two-digit years (09/01/26) converted to full format
- **Name format variations**: "SMITH, JORDAN" (last, first) reordered to "Jordan Smith"
- **Ambiguous organizations**: District vs. vendor vs. regional center flagged in warnings
- **Table data**: Tables parsed separately from free text to capture structured fields
- **Validation columns**: Automatically skipped to avoid overwriting formulas
