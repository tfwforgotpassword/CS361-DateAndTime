"""
logic.py - The date and time math for the Date and Time microservivce

Takes python values in and gives python values back. Logic is separate from the
main.py

-----------------------

    A naive datetime has no time zone attached ("2026-11-01 09:30"). 
    An aware datetime carries a tzinfo, so it pins down one exact
    moment ("2026-11-01 09:30 in New York"). Comparing or subtracting a naive
    and an aware datetime raises an error in Python, so this file makes sure
    every datetime it produces is aware.

    Names like "America/New_York" or "Europe/London" from the IANA time zone
    database. Python's built-in `zoneinfo` module understands them, including
    their daylight saving time (DST) rules.

* relativedelta (from the third-party python-dateutil package):
    Python's built-in `timedelta` only understands fixed-length units (days,
    seconds...). It has no idea what "one month" is, because months have
    different lengths. `relativedelta` does calendar-aware math: "+1 month"
    means "same day, next month", and if that day doesn't exist it uses the
    last day of that month instead.
"""

# date      - a calendar date with no time (year, month, day).
# datetime  - a date plus a time of day, optionally with a time zone.
# timezone  - gives us timezone.utc, used by compute_countdown below.
from datetime import date, datetime, timezone

# ZoneInfo               - builds a time zone object from an IANA name.
# ZoneInfoNotFoundError  - raised when that name doesn't exist.
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.relativedelta import relativedelta


class InvalidInputError(Exception):
    """A custom exception for input that is well-formed JSON but makes no
    sense as a date or time zone (e.g. start = "not-a-date").

    Defining our own exception type lets main.py catch exactly this kind of
    problem and turn it into a friendly HTTP 400 response, while other
    unexpected errors are handled differently (HTTP 500).
    """

    # `str | None` means "a string or None" (Python 3.10+ syntax).
    # `field` records WHICH input was wrong so the client can be told.
    def __init__(self, message: str, field: str | None = None):
        # Note: this doesn't call super().__init__(). That works here because
        # we only ever read .message and .field, but calling
        # super().__init__(message) is a good habit so str(exc) also works.
        self.message = message
        self.field = field


def parse_zone(tz_name: str, field: str = "time_zone") -> ZoneInfo:
    """Turn a time zone name like "America/New_York" into a ZoneInfo object.

    Raises InvalidInputError if the name isn't a real IANA time zone.

    Args:
        tz_name: the time zone name the client sent.
        field:   the request field name to report in an error. The POST
                 endpoints call it "time_zone"; the current-time endpoint's
                 query parameter is called "timezone". A default value means
                 existing callers don't have to pass it.
    """
    try:
        return ZoneInfo(tz_name)
    # Catching a tuple of exception types handles either one:
    #   ZoneInfoNotFoundError - the name is well-formed but unknown ("Mars/Olympus_Mons").
    #   ValueError            - the name is malformed (e.g. an empty string or "../x").
    except (ZoneInfoNotFoundError, ValueError):
        # Translate the low-level library error into OUR error type, with a
        # clear message and the name of the offending field.
        raise InvalidInputError(
            f"'{tz_name}' is not a valid IANA time zone", field=field
        )


def get_current_time(tz: ZoneInfo) -> dict:
    """Return the current date and time in the zone `tz`, ready to send.

    Example result for tz = America/Los_Angeles:
        {"date": "2026-11-16", "time": "16:52:03",
         "utc_offset": "-08:00", "iso_datetime": "2026-11-16T16:52:03-08:00"}
    """
    # datetime.now(tz) reads the computer's clock and expresses that moment
    # in the given zone (including whether daylight saving time is in effect).
    # .replace(microsecond=0) drops fractions of a second so every field
    # agrees with the HH:MM:SS format promised in the communication contract.
    now = datetime.now(tz).replace(microsecond=0)

    # strftime ("string format time") builds a string from format codes:
    #   %Y = 4-digit year, %m = month, %d = day,
    #   %H = hour (00-23), %M = minute, %S = second,
    #   %z = UTC offset as +HHMM or -HHMM (e.g. "-0800").
    offset = now.strftime("%z")
    return {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        # Insert a colon to turn "-0800" into the more common "-08:00".
        # Slicing: offset[:3] is "-08" and offset[3:] is "00".
        "utc_offset": offset[:3] + ":" + offset[3:],
        # The full ISO 8601 form, handy for programs that want one value.
        "iso_datetime": now.isoformat(),
    }


def parse_dt(value: str, tz: ZoneInfo, field: str) -> datetime:
    """Parse an ISO 8601 date or datetime string into an aware datetime.

    Accepted examples:
        "2026-01-15"                 (date only -> midnight)
        "2026-01-15T09:30:00"        (no offset -> interpreted in `tz`)
        "2026-01-15T09:30:00-05:00"  (explicit offset -> kept as-is)

    Args:
        value: the string the client sent.
        tz:    the zone to attach if the string has no offset of its own.
        field: the request field name ("start" or "end"), used in error messages.
    """
    try:
        # First attempt: parse as a full datetime.
        # (On Python 3.11+ this also accepts a plain date like "2026-01-15"
        # and returns midnight on that date.)
        dt = datetime.fromisoformat(value)
    except ValueError:
        # Second attempt (mainly for older Python versions):
        # parse as a date, then combine it with midnight.
        # datetime.min.time() is 00:00:00.
        try:
            dt = datetime.combine(date.fromisoformat(value), datetime.min.time())
        except ValueError:
            # Both attempts failed - the string isn't a valid date at all.
            raise InvalidInputError(
                f"'{value}' is not a valid ISO 8601 date or datetime", field=field
            )

    # If the string had no offset, the datetime is naive. .replace(tzinfo=tz)
    # attaches the requested time zone WITHOUT changing the clock time.
    # (Compare with .astimezone(tz), which would convert the clock time.)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt


def apply_duration(start: datetime, duration: dict, sign: int) -> tuple[datetime, bool]:
    """Add (sign=1) or subtract (sign=-1) a duration from `start`.

    Using a `sign` parameter lets ONE function serve both the add and the
    subtract endpoints instead of writing two nearly identical functions.

    Returns a tuple of two values: (result, clamped).
        result  - the new datetime.
        clamped - True if the day had to be moved because the target month
                  is too short (e.g. Jan 31 + 1 month -> Feb 28).
    """
    # Build one relativedelta holding every unit. Multiplying by `sign`
    # flips every value negative when subtracting for duration.get("years", 0) reads the key, or uses 0 if it's missing.
    delta = relativedelta(
        years=sign * duration.get("years", 0),
        months=sign * duration.get("months", 0),
        days=sign * duration.get("days", 0),
        hours=sign * duration.get("hours", 0),
        minutes=sign * duration.get("minutes", 0),
        seconds=sign * duration.get("seconds", 0),
    )

    # relativedelta applies the larger units first (years, then months),
    # clamps the day if needed, and then adds days/hours/minutes/seconds.
    #
    # Note on time zones: adding hours to an aware datetime moves the CLOCK
    # time ("wall time"). If the span crosses a daylight saving change, the
    # real elapsed time can differ by an hour. Converting to UTC first, doing
    # the math, then converting back would give exact elapsed time instead.
    result = start + delta

    # relativedelta clamps automatically (Jan 31 + 1 month -> Feb 28/29).
    # Detect clamping: round-tripping the months/years doesn't restore the day.
    #
    # The idea: undo only the years/months part. If no clamping happened, we
    # land back on the same day-of-month we started on. If the day was pulled
    # back (31 -> 28), undoing the month gives day 28, not 31, so we know.
    #
    # Known limitation: this only undoes years/months, not days or hours. So
    # if the duration ALSO has days/hours (e.g. Jan 15 + 1 month + 3 days),
    # the extra days shift the day-of-month and `clamped` can wrongly be True.
    clamped = False
    # Only months and years can cause clamping, so only check when they're used.
    if duration.get("months") or duration.get("years"):
        round_trip = result - relativedelta(
            years=sign * duration.get("years", 0),
            months=sign * duration.get("months", 0),
        )
        clamped = round_trip.day != start.day
    return result, clamped


def compute_difference(start: datetime, end: datetime):
    """Measure the gap between two datetimes.

    Returns a 4-item tuple: (sign, calendar_breakdown_dict, total_days, total_seconds)
        sign          - 1 if end >= start, otherwise -1.
        calendar      - {"years", "months", "days"}
        total_days    - whole days between them.
        total_seconds - total seconds between them.

    All the numbers are always positive; `sign` alone tells the direction.
    """
    sign = 1
    a, b = start, end
    # If the dates were given in reverse order, swap them so `a` is the
    # earlier one. This keeps every number positive and simplifies the math.
    if b < a:
        a, b = b, a   # Tuple unpacking: swaps two variables in one line.
        sign = -1

    # relativedelta(later, earlier) - note it takes TWO datetimes here, not
    # keyword amounts - computes the calendar difference: e.g. 2024-02-29 to
    # 2026-03-01 is 2 years, 0 months, 1 day.
    cal = relativedelta(b, a)

    # Subtracting two datetimes gives a built-in `timedelta`, which stores the gap as days + seconds.
    #
    # Note: when both datetimes share the same tzinfo object (which happens
    # when both were parsed with the request's time_zone), Python subtracts
    # their clock times and ignores any DST change between them. Converting
    # both to UTC first (a.astimezone(timezone.utc)) would count real elapsed time.
    delta = b - a
    return (
        sign,
        {"years": cal.years, "months": cal.months, "days": cal.days},
        delta.days,                      # Whole days only (partial days dropped).
        int(delta.total_seconds()),      # total_seconds() returns a float; int() trims it.
    )



def difference_in_unit(calendar: dict, total_days: int, unit: str) -> int:
    """Express a difference (from compute_difference) in one unit.

    Returns the number of FULL units that fit in the gap; any partial unit is
    dropped. For 2024-02-29 -> 2026-03-01 (731 days):
        days -> 731, weeks -> 104, months -> 24, years -> 2

    Days and weeks are fixed lengths, so they come from total_days.
    Months and years vary in length, so they come from the calendar breakdown
    instead (e.g. Jan 31 -> Feb 28 is not a full month, but Feb 28 -> Mar 28 is).
    """
    if unit == "days":
        return total_days
    if unit == "weeks":
        # `//` is floor division: it divides and drops the remainder (10 // 7 == 1).
        return total_days // 7
    if unit == "months":
        return calendar["years"] * 12 + calendar["months"]
    if unit == "years":
        return calendar["years"]
    # Unreachable through the API: Pydantic only lets the four units above
    # through. Raising here protects any other code that calls this function.
    raise ValueError(f"unknown unit: {unit}")


# How many seconds are in each countdown unit. A dictionary like this is a
# simple "lookup table": SECONDS_PER_UNIT["hours"] gives 3600.
# The all-caps name is a Python convention for a value that never changes.
SECONDS_PER_UNIT = {"days": 86400, "hours": 3600, "minutes": 60, "seconds": 1}


def compute_countdown(target: datetime, now: datetime) -> dict:
    """Work out how much time is left until `target`.

    `now` is passed in instead of read inside this function. That makes the
    function easy to test: a test can pass a fixed "now" and know exactly what
    answer to expect. (The route in main.py passes the real current time.)

    Returns:
        {"has_passed": False,
         "remaining": {"days": 38, "hours": 7, "minutes": 5, "seconds": 12},
         "total_seconds": 3308712}

    If the target has already been reached, has_passed is True and every
    number is 0, so a program displaying the countdown simply shows zero.
    """
    # Convert both moments to UTC before subtracting. This counts the REAL time
    # between them, so a countdown that crosses a daylight saving change is
    # still correct (e.g. on the night clocks fall back, 00:00 to 03:00 local
    # time is actually 4 hours, not 3).
    delta = target.astimezone(timezone.utc) - now.astimezone(timezone.utc)

    # Whole seconds only; max(..., 0) stops the countdown going negative.
    total_seconds = max(int(delta.total_seconds()), 0)

    # divmod(a, b) returns two values at once: (a // b, a % b),
    # i.e. how many whole b's fit in a, and what is left over.
    # Peeling off days, then hours, then minutes splits the total into parts.
    days, rest = divmod(total_seconds, SECONDS_PER_UNIT["days"])
    hours, rest = divmod(rest, SECONDS_PER_UNIT["hours"])
    minutes, seconds = divmod(rest, SECONDS_PER_UNIT["minutes"])

    return {
        "has_passed": total_seconds == 0,
        "remaining": {"days": days, "hours": hours, "minutes": minutes, "seconds": seconds},
        "total_seconds": total_seconds,
    }


def countdown_in_unit(total_seconds: int, unit: str) -> int:
    """Express a countdown in one unit, as FULL units (partial units dropped).

    Example: 3308712 seconds -> days: 38, hours: 919, minutes: 55145.
    """
    return total_seconds // SECONDS_PER_UNIT[unit]
