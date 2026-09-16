"""Calendar arithmetic shared by the tax rules.

Spanish tax deadlines are expressed in whole months counted *date to date* (the
2-month wash-sale window of Art. 33.5.f, the 36-month ESPP holding period of
Art. 42.3.f), which is not the same as a fixed number of days. Keeping one
implementation here stops the two rules from drifting apart.
"""

import calendar
from datetime import date


def add_months(day: date, months: int) -> date:
    """Shift ``day`` by whole months, clamping to the end of a short month.

    Clamping only ever applies when the target month is shorter than the source
    (31 Jan + 1 month = 28/29 Feb; 29-Feb-2020 + 36 months = 28-Feb-2023). Callers
    treat the returned date as the first day on which the period has elapsed.
    """
    shifted = day.month - 1 + months
    year = day.year + shifted // 12
    month = shifted % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))
