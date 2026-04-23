"""
Authorization Document Reader
Extracts structured data from school district authorization PDFs using Claude API
and writes results to a Google Sheet.

Usage:
    python extract.py                          # Process all PDFs in sample_pdfs/
    python extract.py --pdf path/to/file.pdf   # Process a single PDF
    python extract.py --dry-run                # Extract and print without writing to Sheets
"""

import os
import sys
import json
import logging
import argparse
from pathlib import Path
from datetime import datetime

import pdfplumber
import anthropic
from dotenv import load_dotenv

from sheets_writer import SheetsWriter

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

load_dotenv()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
FIELDS = [
    "student_name",
    "student_id",
    "district",
    "service_type",
    "authorized_hours_per_month",
    "start_date",
    "end_date",
    "authorization_number",
    "case_manager_name",
    "subject_areas",
    "notes",
]

EXTRACTION_PROMPT = """You are a data extraction assistant. Your job is to extract structured fields from school district authorization documents.

These documents authorize tutoring/educational services for students. Different districts format them completely differently - field names vary, layouts vary, some are clean and some are messy.

Extract the following fields from the document text below. Return a JSON object with these exact keys:

- student_name: Full name of the student/client receiving services
- student_id: Student or client ID number
- district: Name of the school district or regional center issuing the authorization
- service_type: Type of service authorized (e.g., "Academic Coaching", "Tutoring", "Speech Therapy")
- authorized_hours_per_month: Number of authorized hours per month (just the number, e.g., "30")
- start_date: Service start date in M/DD/YY format (2-digit year, e.g., 9/01/26)
- end_date: Service end date in M/DD/YY format (2-digit year, e.g., 12/31/26)
- authorization_number: The authorization or reference number for this document
- case_manager_name: Name of the case manager, caseworker, or coordinator
- subject_areas: Academic subjects covered (e.g., "Math, Reading"). If not explicitly stated, infer from service description if possible
- notes: Any important additional details like billing notes, special conditions, frequency requirements, or funding notes. Keep it concise.

RULES:
1. If a field is not found in the document, set its value to null
2. For dates, always convert to M/DD/YY format with 2-digit year (e.g., 9/01/26 not 09/01/2026)
3. For hours, extract just the numeric value per month. If given as total hours over a period, calculate the monthly amount
4. Look for the field under any reasonable name variant (e.g., "caseworker" = "case manager" = "coordinator")
5. The "district" may appear as the issuing organization name at the top of the document, or as a "Regional Center" name

Return ONLY valid JSON. No explanation, no markdown formatting, just the JSON object.
Also include a "warnings" array listing any fields you were unsure about or had to infer.

DOCUMENT TEXT:
{document_text}
"""


# ---------------------------------------------------------------------------
# PDF text extraction
# ---------------------------------------------------------------------------
def extract_text_from_pdf(pdf_path: str) -> str:
    """Extract text from a PDF using pdfplumber. Includes table text."""
    logger.info(f"Extracting text from: {pdf_path}")
    all_text = []

    try:
        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages):
                page_text = page.extract_text() or ""

                # Also try to extract tables for structured data
                tables = page.extract_tables()
                table_text = ""
                for table in tables:
                    for row in table:
                        if row:
                            cleaned = [str(cell).strip() if cell else "" for cell in row]
                            table_text += " | ".join(cleaned) + "\n"

                combined = page_text
                if table_text:
                    combined += "\n\nTABLE DATA:\n" + table_text

                all_text.append(f"--- Page {i + 1} ---\n{combined}")
                logger.info(f"  Page {i + 1}: {len(page_text)} chars text, {len(tables)} tables")

    except Exception as e:
        logger.error(f"Failed to extract text from {pdf_path}: {e}")
        raise

    full_text = "\n\n".join(all_text)

    if not full_text.strip():
        logger.warning(f"No text extracted from {pdf_path} - PDF may be scanned/image-based")

    return full_text


# ---------------------------------------------------------------------------
# LLM extraction via Claude API
# ---------------------------------------------------------------------------
def extract_fields_with_llm(document_text: str, pdf_name: str) -> dict:
    """Send extracted text to Claude and get structured fields back."""
    logger.info(f"Sending to Claude API for extraction...")

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not set in .env file")

    client = anthropic.Anthropic(api_key=api_key)

    prompt = EXTRACTION_PROMPT.format(document_text=document_text)

    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )

        response_text = message.content[0].text.strip()

        # Clean up response - remove markdown code blocks if present
        if response_text.startswith("```"):
            response_text = response_text.split("\n", 1)[1]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
            response_text = response_text.strip()

        result = json.loads(response_text)

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Claude response as JSON: {e}")
        logger.error(f"Raw response: {response_text[:500]}")
        raise
    except anthropic.APIError as e:
        logger.error(f"Claude API error: {e}")
        raise

    # Log warnings from LLM
    warnings = result.pop("warnings", [])
    if warnings:
        for w in warnings:
            logger.warning(f"  LLM warning for {pdf_name}: {w}")

    # Log missing fields
    for field in FIELDS:
        if result.get(field) is None:
            logger.warning(f"  Missing field '{field}' in {pdf_name}")

    return result


# ---------------------------------------------------------------------------
# Process a single PDF end-to-end
# ---------------------------------------------------------------------------
def process_pdf(pdf_path: str) -> dict:
    """Full pipeline: PDF -> text -> LLM -> structured data."""
    pdf_name = Path(pdf_path).name
    logger.info(f"{'='*60}")
    logger.info(f"Processing: {pdf_name}")
    logger.info(f"{'='*60}")

    # Step 1: Extract text
    text = extract_text_from_pdf(pdf_path)
    if not text.strip():
        logger.error(f"Skipping {pdf_name} - no text could be extracted")
        return None

    # Step 2: LLM extraction
    result = extract_fields_with_llm(text, pdf_name)

    # Add metadata
    result["source_file"] = pdf_name
    result["processed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    logger.info(f"Extraction complete for {pdf_name}")
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Authorization Document Reader")
    parser.add_argument("--pdf", type=str, help="Path to a single PDF to process")
    parser.add_argument(
        "--pdf-dir",
        type=str,
        default="sample_pdfs",
        help="Directory containing PDFs to process (default: sample_pdfs/)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Extract and print results without writing to Google Sheets",
    )
    args = parser.parse_args()

    # Collect PDF paths
    if args.pdf:
        pdf_paths = [args.pdf]
    else:
        pdf_dir = Path(args.pdf_dir)
        if not pdf_dir.exists():
            logger.error(f"PDF directory not found: {pdf_dir}")
            sys.exit(1)
        pdf_paths = sorted(pdf_dir.glob("*.pdf"))
        if not pdf_paths:
            logger.error(f"No PDFs found in {pdf_dir}")
            sys.exit(1)

    logger.info(f"Found {len(pdf_paths)} PDF(s) to process")

    # Process each PDF
    results = []
    errors = []
    for pdf_path in pdf_paths:
        try:
            result = process_pdf(str(pdf_path))
            if result:
                results.append(result)
        except Exception as e:
            logger.error(f"Failed to process {pdf_path}: {e}")
            errors.append({"file": str(pdf_path), "error": str(e)})

    # Print results
    logger.info(f"\n{'='*60}")
    logger.info(f"RESULTS: {len(results)} successful, {len(errors)} failed")
    logger.info(f"{'='*60}")

    for r in results:
        print(json.dumps(r, indent=2))
        print()

    if errors:
        logger.warning("ERRORS:")
        for e in errors:
            logger.warning(f"  {e['file']}: {e['error']}")

    # Write to Google Sheets
    if not args.dry_run and results:
        try:
            writer = SheetsWriter()
            writer.write_results(results)
            logger.info("Results written to Google Sheet successfully!")
        except Exception as e:
            logger.error(f"Failed to write to Google Sheets: {e}")
            logger.info("Results were printed above - you can copy them manually")
    elif args.dry_run:
        logger.info("Dry run - skipping Google Sheets write")

    return results, errors


if __name__ == "__main__":
    main()
