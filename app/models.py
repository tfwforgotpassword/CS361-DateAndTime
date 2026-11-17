"""
models.py - The "shapes" of the data this API accepts and returns.

Purpose of this file
---------------------
Every request a client sends is JSON, and every is JSON.
This file describes exactly what that JSON must look like, using Pydantic models.

FastAPI receives a request, it hands the JSON body to the matching model.

Pydantic is used to:
-Check that every required field is present.
-Checks that every value has the right type and will convert, if needed (i.e. JSON 5 to python int 5)
-Runs any defined rules (i.e. "must be >= 0").

If any check fails, FastAPI automatically rejects the request with HTTP 422
before the route code ever runs. This is so data validation doesn't need to be handled in main.py or logic.py

Response models work the other way round: they guarantee that what we send
back always has the same, documented shape. FastAPI also reads all of these
models to build the interactive documentation page at /docs.

NAMING PATTERN
--------------
  *Request  -> input (JSON the client sends) 
  *Response -> output (JSON we send back to the client) 
"""

# `Optional[str]` means "a str, or None". It is used for fields the client
# is allowed to leave out.
# `Literal["a", "b"]` means "only exactly one of these values". Pydantic
# rejects anything else with a 422 error, so we never have to check by hand.
from typing import Literal, Optional

# BaseModel      - the parent class all Pydantic models inherit from.
# Field          - lets us attach defaults, limits and descriptions to a field.
# model_validator - a decorator for writing custom checks that look at the
#                   whole model at once (not just one field).
from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Input building block
# ---------------------------------------------------------------------------

class Duration(BaseModel):
    """An amount of time to add or subtract, e.g. {"months": 1, "days": 3}.

    Every unit is optional and defaults to 0, so the client only has to send
    the units it cares about.
    """

    # Field(default=0, ge=0):
    #   default=0 -> if the client leaves this out, use 0.
    #   ge=0      -> "greater than or equal to 0". Negative numbers are rejected
    #                with a 422 error. Direction (add vs. subtract) is chosen by
    #                which endpoint you call, not by the sign of these numbers.
    years: int = Field(default=0, ge=0)
    months: int = Field(default=0, ge=0)
    days: int = Field(default=0, ge=0)
    hours: int = Field(default=0, ge=0)
    minutes: int = Field(default=0, ge=0)
    seconds: int = Field(default=0, ge=0)

    # A model validator runs a custom rule on the whole object.
    # mode="after" means it runs AFTER the individual fields have already been
    # checked and converted, so here every field is guaranteed to be an int >= 0.
    @model_validator(mode="after")
    def at_least_one_positive(self):
        # getattr(self, "years") is the same as self.years; using getattr lets
        # us loop over the field names as strings.
        # all(...) is True only if EVERY field is 0, i.e. the duration is empty.
        if all(getattr(self, f) == 0 for f in
               ("years", "months", "days", "hours", "minutes", "seconds")):
            # Raising ValueError inside a validator makes Pydantic report a
            # validation error, which FastAPI turns into an HTTP 422 response.
            raise ValueError("duration must have at least one field greater than zero")
        # An "after" validator must return the (possibly modified) model.
        return self


# ---------------------------------------------------------------------------
# /api/v1/dates/add  and  /api/v1/dates/subtract
# ---------------------------------------------------------------------------

class AddSubtractRequest(BaseModel):
    """Request body for both the add and subtract endpoints."""

    # `...` (Python's Ellipsis) as the default means "required, no default".
    # The `description` text appears in the auto-generated /docs page.
    # We accept the date as a plain string and parse it ourselves in logic.py,
    # so we can give a clear, custom error message for bad dates (HTTP 400).
    start: str = Field(..., description="ISO 8601 date or datetime")

    # Using another model as a type "nests" it: the JSON must contain a
    # "duration" object, and Pydantic validates it with the Duration rules above.
    duration: Duration

    # Optional with a default: if the client omits time_zone, it becomes "UTC".
    # (Optional also means a client could send null; main.py handles that by
    # falling back to "UTC" with `req.time_zone or "UTC"`.)
    time_zone: Optional[str] = Field(default="UTC")


class AddSubtractResponse(BaseModel):
    """Response body for both the add and subtract endpoints."""

    start: str          # The start value exactly as the client sent it.
    duration: dict      # The duration applied; negative values for subtract.
    result: str         # The answer, as an ISO 8601 string.
    clamped: bool       # True if the day had to be moved to fit a shorter month
                        # (e.g. Jan 31 + 1 month -> Feb 28).
    time_zone: str      # The time zone used for the calculation.


# ---------------------------------------------------------------------------
# /api/v1/dates/difference
# ---------------------------------------------------------------------------

# A type alias: give a long type a short name so it can be reused.
DifferenceUnit = Literal["days", "weeks", "months", "years"]


class DifferenceRequest(BaseModel):
    """Request body for the difference endpoint."""

    # No Field(...) needed: a plain type hint with no default is required.
    start: str
    end: str
    time_zone: Optional[str] = Field(default="UTC")

    # Optional: ask for the difference in one unit. Leaving it out (or sending
    # null) is allowed; any value other than the four units gets a 422 error.
    unit: Optional[DifferenceUnit] = Field(
        default=None, description="Also express the difference in this unit"
    )


class CalendarBreakdown(BaseModel):
# Output would look like "2 years, 0 months and 1 day"
    years: int
    months: int
    days: int


class DifferenceResponse(BaseModel):
    """Response body for the difference endpoint."""

    start: str
    end: str
    sign: int                     # 1 if end is after (or equal to) start, -1 if before.
    calendar: CalendarBreakdown   # Nested model -> nested JSON object.
    total_days: int               # Whole days between the two moments.
    total_seconds: int            # Total seconds between the two moments.
    # Only filled in when the request included `unit`; otherwise both are
    # None, which appears as null in the JSON.
    unit: Optional[str] = None    # The unit that was asked for, i.e. "weeks".
    value: Optional[int] = None   # Full units in the gap, e.g. 104 weeks.


# ---------------------------------------------------------------------------
# /api/v1/datetime/current
# ---------------------------------------------------------------------------
#
# There is no request model here: this endpoint is a GET request, and GET requests send their input in the 
# URL's query string (e.g. ?timezone=America/Los_Angeles) instead of a JSON body. main.py reads that query parameter directly.

class CurrentTimeResponse(BaseModel):
    """Response body for the current-time lookup."""

    timezone: str       # The IANA time zone used, e.g. "America/Los_Angeles".
    date: str           # Current date in that zone, YYYY-MM-DD.
    time: str           # Current time in that zone, HH:MM:SS (24-hour clock).
    utc_offset: str     # Offset from UTC right now, e.g. "-08:00". It changes
                        # when daylight saving time starts or ends.
    iso_datetime: str   # All of the above in one ISO 8601 string,
                        # e.g. "2026-11-16T16:52:03-08:00".


# ---------------------------------------------------------------------------
# /api/v1/datetime/countdown
# ---------------------------------------------------------------------------
#
# Also a GET request, so again no request model: main.py reads the
# `target`, `timezone` and `unit` query parameters directly.

CountdownUnit = Literal["days", "hours", "minutes", "seconds"]


class CountdownBreakdown(BaseModel):
    """Time remaining split into parts, like a countdown clock shows it."""

    days: int
    hours: int      # 0-23
    minutes: int    # 0-59
    seconds: int    # 0-59


class CountdownResponse(BaseModel):
    """Response body for the countdown endpoint."""

    target: str                     # The target exactly as the client sent it.
    timezone: str                   # The time zone used.
    now: str                        # The moment the countdown was measured from.
    has_passed: bool                # True once the target has been reached.
    remaining: CountdownBreakdown   # Nested model -> nested JSON object.
    total_seconds: int              # All the remaining time, in seconds.
    # Only filled in when the request included `unit` (null otherwise).
    unit: Optional[str] = None      # e.g. "hours"
    value: Optional[int] = None     # Full units remaining, e.g. 919 (hours).


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    """Response body for the health check. Other programs (or a monitoring
    dashboard) call /health to confirm the service is up."""

    status: str     # Always "ok" when the service is running.
    service: str    # The service's name.
    version: str    # The API version.
    utc_time: str   # The server's current time in UTC.


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class ErrorResponse(BaseModel):
    """The single, consistent shape of every error this API returns.

    main.py builds these error bodies by hand in its exception handlers. This
    class documents that shape in one place (and is a natural thing to
    reference in the OpenAPI spec). Using one error format everywhere makes
    life easy for clients: they only need one piece of code to handle errors.
    """

    error_code: str               # Machine-readable code, e.g. "INVALID_INPUT".
    message: str                  # Human-readable explanation.
    field: Optional[str] = None   # Which input caused the problem, if known.
