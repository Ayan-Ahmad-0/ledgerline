import pytest
from decimal import Decimal
from datetime import datetime, date, timezone
import zoneinfo

from app.scoring.comparators import (
    parse_money,
    compare_money,
    parse_date,
    compare_dates,
    normalize_vendor_name,
    compare_strings,
    NormalizedMoney,
    NormalizedDate,
)
from app.scoring.metrics import TypedScorer, FieldScoreResult
from app.timefiscal.tz import TimezoneInstant, make_instant, parse_instant_iso
from app.timefiscal.fiscal import HardcodedFiscalCalendar, default_fiscal_calendar


# ===========================================================================
# 1. Typed Comparators & Normalization Tests (Directive #3)
# ===========================================================================

def test_money_parsing_and_comparison():
    m1 = parse_money("$1,234.56")
    assert m1 == NormalizedMoney(amount=Decimal("1234.56"), currency="USD")

    m2 = parse_money("1234.56 USD")
    assert compare_money(m1, m2)

    # European format with decimal comma
    m3 = parse_money("€ 1.234,56")
    assert m3 == NormalizedMoney(amount=Decimal("1234.56"), currency="EUR")
    assert not compare_money(m1, m3)  # Different currency

    # Negative amounts in parentheses
    m_neg = parse_money("($45.00)")
    assert m_neg.amount == Decimal("-45.00")

    # Tolerance comparison
    m4 = NormalizedMoney(amount=Decimal("100.00"), currency="USD")
    m5 = NormalizedMoney(amount=Decimal("100.009"), currency="USD")
    assert compare_money(m4, m5, tolerance=Decimal("0.01"))


def test_date_parsing_ambiguity_tracking():
    # Unambiguous ISO date
    d_iso = parse_date("2024-03-15")
    assert not d_iso.is_ambiguous
    assert d_iso.iso_date == "2024-03-15"

    # Unambiguous because day > 12: '25/03/2024'
    d_unambig = parse_date("25/03/2024")
    assert not d_unambig.is_ambiguous
    assert d_unambig.iso_date == "2024-03-25"

    # Genuine ambiguity without locale: '03/04/2024' (could be March 4 or April 3)
    d_ambig = parse_date("03/04/2024", locale=None)
    assert d_ambig.is_ambiguous
    assert "2024-03-04" in d_ambig.ambiguous_candidates
    assert "2024-04-03" in d_ambig.ambiguous_candidates

    # Resolved by locale: en_US -> MM/DD/YYYY (March 4)
    d_us = parse_date("03/04/2024", locale="en_US")
    assert not d_us.is_ambiguous
    assert d_us.iso_date == "2024-03-04"

    # Resolved by locale: en_GB -> DD/MM/YYYY (April 3)
    d_gb = parse_date("03/04/2024", locale="en_GB")
    assert not d_gb.is_ambiguous
    assert d_gb.iso_date == "2024-04-03"

    # Ambiguous date comparison matches either candidate
    assert compare_dates(d_ambig, d_us)
    assert compare_dates(d_ambig, d_gb)


def test_vendor_legal_suffix_normalization():
    v1 = "Acme Supply Ltd."
    v2 = "acme supply LLC"
    v3 = "ACME SUPPLY, INC"
    v4 = "Acme Supply Corporation"

    assert normalize_vendor_name(v1) == "acme supply"
    assert compare_strings(v1, v2, vendor_mode=True)
    assert compare_strings(v2, v3, vendor_mode=True)
    assert compare_strings(v3, v4, vendor_mode=True)


def test_cord_dataset_evaluation_rule():
    # Allowed fields on CORD
    assert TypedScorer.evaluate_field("subtotal", "$10.00", "10.00 USD", dataset_name="CORD")
    assert TypedScorer.evaluate_field("tax", "$1.00", "$1.00", dataset_name="CORD")

    # Forbidden fields on CORD: Must raise ValueError according to specification
    with pytest.raises(ValueError) as exc_info:
        TypedScorer.evaluate_field("vendor_name", "Acme", "Acme", dataset_name="CORD")
    assert "CORD dataset does not label" in str(exc_info.value)

    with pytest.raises(ValueError):
        TypedScorer.evaluate_field("invoice_date", "2024-01-01", "2024-01-01", dataset_name="CORD")


# ===========================================================================
# 2. Timezone & Fiscal Correctness Tests (Directive #4)
# ===========================================================================

def test_leap_day_feb_29_fiscal_resolution():
    calendar = HardcodedFiscalCalendar(start_month=10)

    # 2024 is a leap year; Feb 29, 2024
    leap_date = date(2024, 2, 29)
    period = calendar.get_period(leap_date)

    assert period.calendar_year == 2024
    assert period.calendar_month == 2
    assert period.calendar_day == 29
    assert period.fiscal_year == 2024
    assert period.quarter == 2  # Feb belongs to Q2 (Jan-Mar)
    assert period.period_code == "FY2024-Q2"


def test_daylight_saving_transitions():
    tz_ny = "America/New_York"

    # Before spring-forward (Standard Time EST: UTC-5)
    dt_winter = datetime(2024, 3, 9, 12, 0, 0, tzinfo=zoneinfo.ZoneInfo(tz_ny))
    inst_winter = make_instant(dt_winter, tz_ny)
    assert inst_winter.utc_offset_minutes == -300  # -5 hours

    # After spring-forward (Daylight Time EDT: UTC-4)
    dt_summer = datetime(2024, 3, 11, 12, 0, 0, tzinfo=zoneinfo.ZoneInfo(tz_ny))
    inst_summer = make_instant(dt_summer, tz_ny)
    assert inst_summer.utc_offset_minutes == -240  # -4 hours

    # Fall-back in November (EDT -> EST)
    dt_fall = datetime(2024, 11, 4, 12, 0, 0, tzinfo=zoneinfo.ZoneInfo(tz_ny))
    inst_fall = make_instant(dt_fall, tz_ny)
    assert inst_fall.utc_offset_minutes == -300  # Back to -5 hours


def test_cross_timezone_period_resolution():
    """
    At the exact same absolute UTC instant (2023-09-30 23:30:00 UTC):
    - In New York (EDT, UTC-4), local time is Sept 30, 2023, 19:30 -> FY2023 Q4 (calendar end of FY).
    - In Tokyo (JST, UTC+9), local time is Oct 1, 2023, 08:30 -> FY2024 Q1 (new fiscal year!).
    """
    calendar = HardcodedFiscalCalendar(start_month=10)
    utc_moment = datetime(2023, 9, 30, 23, 30, 0, tzinfo=timezone.utc)

    # In New York
    ny_instant = TimezoneInstant(utc_instant=utc_moment, source_timezone="America/New_York")
    ny_period = calendar.resolve_instant(ny_instant, use_local_date=True)

    assert ny_period.calendar_year == 2023
    assert ny_period.calendar_month == 9
    assert ny_period.calendar_day == 30
    assert ny_period.fiscal_year == 2023
    assert ny_period.quarter == 4
    assert ny_period.period_code == "FY2023-Q4"

    # In Tokyo
    tokyo_instant = TimezoneInstant(utc_instant=utc_moment, source_timezone="Asia/Tokyo")
    tokyo_period = calendar.resolve_instant(tokyo_instant, use_local_date=True)

    assert tokyo_period.calendar_year == 2023
    assert tokyo_period.calendar_month == 10
    assert tokyo_period.calendar_day == 1
    assert tokyo_period.fiscal_year == 2024
    assert tokyo_period.quarter == 1
    assert tokyo_period.period_code == "FY2024-Q1"

    # Verifying that identical UTC instant resolves to different fiscal periods when based on local transaction dates!
    assert ny_period.period_code != tokyo_period.period_code


def test_year_boundary_totals():
    calendar = HardcodedFiscalCalendar(start_month=10)

    # Dec 31, 2023 -> Belongs to FY2024 Q1 (Oct-Dec)
    dec_31 = date(2023, 12, 31)
    period_dec = calendar.get_period(dec_31)
    assert period_dec.fiscal_year == 2024
    assert period_dec.quarter == 1

    # Jan 1, 2024 -> Belongs to FY2024 Q2 (Jan-Mar)
    jan_1 = date(2024, 1, 1)
    period_jan = calendar.get_period(jan_1)
    assert period_jan.fiscal_year == 2024
    assert period_jan.quarter == 2
    assert period_jan.fiscal_year == period_dec.fiscal_year
