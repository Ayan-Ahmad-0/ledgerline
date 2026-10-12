import re
from decimal import Decimal, InvalidOperation
from typing import Optional, Any
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Money Comparator & Normalization
# ---------------------------------------------------------------------------

CURRENCY_SYMBOL_MAP = {
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
    "₹": "INR",
    "C$": "CAD",
    "A$": "AUD",
    "CHF": "CHF",
}

COMMON_CURRENCY_CODES = {"USD", "EUR", "GBP", "JPY", "INR", "CAD", "AUD", "CHF"}


class NormalizedMoney(BaseModel):
    amount: Decimal
    currency: str = "USD"

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, NormalizedMoney):
            return self.currency == other.currency and self.amount == other.amount
        return False


def parse_money(raw_value: str, default_currency: str = "USD") -> Optional[NormalizedMoney]:
    """
    Parse strings like '$1,234.56', '1234.56 EUR', '(50.00)', '-$25.50'
    into NormalizedMoney(amount=Decimal(...), currency=...).
    """
    if not raw_value or not raw_value.strip():
        return None

    cleaned = raw_value.strip()

    # Detect currency
    currency = default_currency
    for symbol, code in CURRENCY_SYMBOL_MAP.items():
        if symbol in cleaned:
            currency = code
            cleaned = cleaned.replace(symbol, "")
            break

    # Look for trailing/leading 3-letter currency code (e.g., '100 USD')
    code_match = re.search(r"\b([A-Z]{3})\b", cleaned)
    if code_match and code_match.group(1) in COMMON_CURRENCY_CODES:
        currency = code_match.group(1)
        cleaned = re.sub(r"\b[A-Z]{3}\b", "", cleaned)

    # Check for negative amounts indicated by parentheses: '(12.34)'
    is_negative = False
    cleaned = cleaned.strip()
    if cleaned.startswith("(") and cleaned.endswith(")"):
        is_negative = True
        cleaned = cleaned[1:-1].strip()
    elif "-" in cleaned:
        is_negative = True
        cleaned = cleaned.replace("-", "").strip()

    # Normalize European decimal comma if e.g. '1.234,56'
    if re.search(r"^\d{1,3}(\.\d{3})*,\d{2}$", cleaned):
        cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        # Standard US format '1,234.56'
        cleaned = cleaned.replace(",", "")

    # Clean any leftover non-numeric characters except '.'
    cleaned = re.sub(r"[^\d.]", "", cleaned)

    if not cleaned:
        return None

    try:
        amount = Decimal(cleaned)
        if is_negative:
            amount = -amount
        return NormalizedMoney(amount=amount.quantize(Decimal("0.01")), currency=currency)
    except (InvalidOperation, ValueError):
        return None


def compare_money(
    expected: NormalizedMoney,
    actual: NormalizedMoney,
    tolerance: Decimal = Decimal("0.00")
) -> bool:
    """Compare two NormalizedMoney objects with currency equality and decimal tolerance."""
    if expected.currency != actual.currency:
        return False
    return abs(expected.amount - actual.amount) <= tolerance


# ---------------------------------------------------------------------------
# Date Normalization & Locale Ambiguity Tracking
# ---------------------------------------------------------------------------

class NormalizedDate(BaseModel):
    iso_date: str  # YYYY-MM-DD
    year: int
    month: int
    day: int
    is_ambiguous: bool = False
    ambiguous_candidates: list[str] = Field(default_factory=list)


def parse_date(raw_value: str, locale: Optional[str] = None) -> Optional[NormalizedDate]:
    """
    Parse date string into ISO date.
    Critique requirement: Record genuine ambiguity instead of guessing when
    numbers could represent either MM/DD or DD/MM and locale is unspecified.
    """
    if not raw_value or not raw_value.strip():
        return None

    cleaned = raw_value.strip()

    # Pattern: YYYY-MM-DD or YYYY/MM/DD (unambiguous)
    iso_match = re.search(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b", cleaned)
    if iso_match:
        y, m, d = int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3))
        try:
            iso_str = f"{y:04d}-{m:02d}-{d:02d}"
            # Basic validation
            if 1 <= m <= 12 and 1 <= d <= 31:
                return NormalizedDate(iso_date=iso_str, year=y, month=m, day=d, is_ambiguous=False)
        except ValueError:
            return None

    # Two-digit/one-digit parts: A/B/YYYY or A-B-YYYY
    slash_match = re.search(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b", cleaned)
    if slash_match:
        p1, p2, y = int(slash_match.group(1)), int(slash_match.group(2)), int(slash_match.group(3))

        # Case 1: One part is > 12 -> unambiguous
        if p1 > 12 and 1 <= p2 <= 12:
            # DD/MM/YYYY
            return NormalizedDate(
                iso_date=f"{y:04d}-{p2:02d}-{p1:02d}",
                year=y,
                month=p2,
                day=p1,
                is_ambiguous=False
            )
        elif p2 > 12 and 1 <= p1 <= 12:
            # MM/DD/YYYY
            return NormalizedDate(
                iso_date=f"{y:04d}-{p1:02d}-{p2:02d}",
                year=y,
                month=p1,
                day=p2,
                is_ambiguous=False
            )

        # Case 2: Both p1 <= 12 and p2 <= 12 (potential genuine ambiguity)
        if 1 <= p1 <= 12 and 1 <= p2 <= 12:
            cand1 = f"{y:04d}-{p1:02d}-{p2:02d}"  # MM/DD/YYYY
            cand2 = f"{y:04d}-{p2:02d}-{p1:02d}"  # DD/MM/YYYY

            if p1 == p2:
                # e.g., 03/03/2024 - same day and month
                return NormalizedDate(
                    iso_date=cand1,
                    year=y,
                    month=p1,
                    day=p2,
                    is_ambiguous=False
                )

            if locale == "en_US":
                return NormalizedDate(
                    iso_date=cand1,
                    year=y,
                    month=p1,
                    day=p2,
                    is_ambiguous=False
                )
            elif locale in ("en_GB", "en_EU", "fr_FR", "de_DE"):
                return NormalizedDate(
                    iso_date=cand2,
                    year=y,
                    month=p2,
                    day=p1,
                    is_ambiguous=False
                )
            else:
                # Unspecified locale with distinct candidates -> genuine ambiguity!
                return NormalizedDate(
                    iso_date=cand1,
                    year=y,
                    month=p1,
                    day=p2,
                    is_ambiguous=True,
                    ambiguous_candidates=[cand1, cand2]
                )

    return None


def compare_dates(expected: NormalizedDate, actual: NormalizedDate) -> bool:
    """Compare two NormalizedDates, taking ambiguity candidates into account."""
    if expected.iso_date == actual.iso_date:
        return True

    # If either date is ambiguous, match if candidate lists overlap
    if expected.is_ambiguous and actual.iso_date in expected.ambiguous_candidates:
        return True
    if actual.is_ambiguous and expected.iso_date in actual.ambiguous_candidates:
        return True

    return False


# ---------------------------------------------------------------------------
# String Normalization (Legal Entity Suffixes, Whitespace, Case)
# ---------------------------------------------------------------------------

LEGAL_SUFFIXES = {
    "ltd", "limited", "inc", "incorporated", "llc", "corp", "corporation",
    "gmbh", "co", "company", "plc", "sa", "sarl", "bv", "nv", "pvt"
}


def normalize_string(text: str) -> str:
    """Lowercase and collapse whitespace."""
    if not text:
        return ""
    cleaned = text.lower()
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def normalize_vendor_name(name: str) -> str:
    """
    Normalize company names by stripping punctuation, extra whitespace,
    and trailing legal entity suffixes like 'Ltd', 'LLC', 'Inc', etc.
    """
    cleaned = normalize_string(name)
    # Remove punctuation
    cleaned = re.sub(r"[^\w\s]", "", cleaned)

    tokens = cleaned.split()
    while tokens and tokens[-1] in LEGAL_SUFFIXES:
        tokens.pop()

    return " ".join(tokens)


def compare_strings(
    expected: str,
    actual: str,
    vendor_mode: bool = False
) -> bool:
    """Typed string comparator with optional vendor legal entity suffix stripping."""
    if vendor_mode:
        return normalize_vendor_name(expected) == normalize_vendor_name(actual)
    return normalize_string(expected) == normalize_string(actual)
