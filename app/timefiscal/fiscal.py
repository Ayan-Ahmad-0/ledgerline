from datetime import datetime, date, timezone
from dataclasses import dataclass
from typing import Optional

from app.timefiscal.tz import TimezoneInstant


@dataclass(frozen=True)
class FiscalPeriod:
    fiscal_year: int
    quarter: int       # 1, 2, 3, 4
    fiscal_month: int  # 1 to 12 relative to fiscal year start
    period_code: str   # e.g., 'FY2024-Q1'
    calendar_year: int
    calendar_month: int
    calendar_day: int


class HardcodedFiscalCalendar:
    """
    Hardcoded fiscal calendar engine.
    Default starts October 1st (standard US Federal fiscal calendar).
    - Q1: Oct, Nov, Dec (Months 10, 11, 12 of year Y -> FY Y+1)
    - Q2: Jan, Feb, Mar (Months 1, 2, 3 of year Y+1 -> FY Y+1)
    - Q3: Apr, May, Jun (Months 4, 5, 6 of year Y+1 -> FY Y+1)
    - Q4: Jul, Aug, Sep (Months 7, 8, 9 of year Y+1 -> FY Y+1)
    """

    def __init__(self, start_month: int = 10):
        if not 1 <= start_month <= 12:
            raise ValueError("Fiscal start month must be between 1 and 12.")
        self.start_month = start_month

    def get_period(self, target_date: date) -> FiscalPeriod:
        year = target_date.year
        month = target_date.month
        day = target_date.day

        # Calculate month offset relative to start_month
        if month >= self.start_month:
            # Belongs to next calendar year's fiscal year
            fiscal_year = year + (1 if self.start_month > 1 else 0)
            fiscal_month_idx = month - self.start_month + 1
        else:
            fiscal_year = year
            fiscal_month_idx = (12 - self.start_month + 1) + month

        # Determine Quarter (1 to 4)
        quarter = (fiscal_month_idx - 1) // 3 + 1

        period_code = f"FY{fiscal_year}-Q{quarter}"

        return FiscalPeriod(
            fiscal_year=fiscal_year,
            quarter=quarter,
            fiscal_month=fiscal_month_idx,
            period_code=period_code,
            calendar_year=year,
            calendar_month=month,
            calendar_day=day,
        )

    def resolve_instant(
        self,
        instant: TimezoneInstant,
        use_local_date: bool = True
    ) -> FiscalPeriod:
        """
        Assign reporting period for a TimezoneInstant.
        If use_local_date=True, evaluates the calendar date in the source timezone.
        If use_local_date=False, evaluates the UTC calendar date.
        """
        if use_local_date:
            dt = instant.local_datetime
        else:
            dt = instant.utc_instant

        return self.get_period(dt.date())


# Default calendar singleton instance
default_fiscal_calendar = HardcodedFiscalCalendar(start_month=10)
