"""
main.py - The entry point of the web service.

Purpose of this file
-------------------
-Creates the FastAPI application object (`app`).
-Plugs in the middleware (middleware.py).
-Registers exception handlers that turn errors into JSON.
-Defines the routes (URLs) and connects each one to the date logic.

HOW TO RUN IT
-------------
From the project folder (with the virtual environment active):

    uvicorn app.main:app --port 8000 --reload

  * `app.main` -> the module app/main.py
  * `:app`     -> the variable named `app` inside it
  * `--port 8000` -> the port named in the communication contract (README.md).
                     Teammates' programs connect to this port, so keep it.
  * `--reload` -> restart automatically when you save a file (development only)

Then open http://localhost:8000/docs for interactive documentation that
FastAPI generates automatically from the routes and models below.

HOW A REQUEST FLOWS THROUGH THE APP
-----------------------------------
  1. Uvicorn (the web server) receives the HTTP request.
  2. ProcessTimeMiddleware starts its timer.
  3. FastAPI matches the URL + HTTP method to one of the routes below.
  4. FastAPI validates the input: the JSON body (POST routes) or the query
     string (GET routes) against what the route function declares.
     - Invalid shape -> RequestValidationError -> 422 (handler below).
  5. The route function runs, calling functions in logic.py.
     - Bad date/time zone -> InvalidInputError -> 400 (handler below).
     - Anything unexpected  -> Exception -> 500 (handler below).
  6. The returned model is converted to JSON and sent back, with the
     timing header added by the middleware on the way out.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

# Import the pieces from our own modules. The `app.` prefix works because
# the `app` folder contains an __init__.py, which makes it a Python package.
from app.logic import (
    InvalidInputError,
    apply_duration,
    compute_countdown,
    compute_difference,
    countdown_in_unit,
    difference_in_unit,
    get_current_time,
    parse_dt,
    parse_zone,
)
from app.middleware import ProcessTimeMiddleware
from app.models import (
    AddSubtractRequest,
    AddSubtractResponse,
    CountdownResponse,
    CountdownUnit,
    CurrentTimeResponse,
    DifferenceRequest,
    DifferenceResponse,
    HealthResponse,
)

# Keeping the version in one constant means it's defined in a single place
# and used both in the app metadata and in the /health response.
VERSION = "1.0.0"

# Create the application. title/version/description are not required, but
# they appear at the top of the auto-generated /docs page and in the
# OpenAPI spec FastAPI serves at /openapi.json.
app = FastAPI(
    title="Date and Time Tracker API",
    version=VERSION,
    description="Stateless microservice for calendar-accurate date/time arithmetic.",
)

# Register the middleware so it wraps every request (see middleware.py for more deatails on functionality).
app.add_middleware(ProcessTimeMiddleware)


# ---------- Error handling: consistent JSON error bodies ----------
#
# An exception handler is a function FastAPI calls when a route (or FastAPI
# itself) raises a particular exception type. Instead of letting the error
# crash the request or return FastAPI's default format, each handler builds
# a JSONResponse in OUR standard error shape:
#
#     {"error_code": "...", "message": "...", "field": "..." or null}
#
# One consistent format means client programs need only one piece of code
# to handle every kind of error.
#
# HTTP status codes used:
#   400 Bad Request          - the request is well-formed but the values make no sense.
#   422 Unprocessable Content - the JSON doesn't match the expected structure/types.
#   500 Internal Server Error - a bug or unexpected failure on our side.

# The decorator @app.exception_handler(X) tells FastAPI:
# "whenever exception type X is raised, call this function to build the response".
@app.exception_handler(InvalidInputError)
async def invalid_input_handler(request: Request, exc: InvalidInputError):
    # `exc` is the InvalidInputError raised in logic.py, so we can read the
    # message and field name that were stored on it.
    return JSONResponse(
        status_code=400,
        content={"error_code": "INVALID_INPUT", "message": exc.message, "field": exc.field},
    )


# RequestValidationError is raised by FastAPI itself when the request body or
# query string fails validation (missing field, wrong type, failed custom rule).
@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    # exc.errors() returns a list of problems. Each is a dict such as:
    #   {"loc": ("body", "duration", "days"), "msg": "Input should be a valid integer", ...}
    # We report just the first problem to keep the response simple.
    # `X if condition else Y` is Python's one-line if/else (a "ternary").
    first = exc.errors()[0] if exc.errors() else {}

    # "loc" is the path to the bad value. It starts with where the value came
    # from: "body" (JSON body) or "query" (URL query string). We drop that and
    # join the rest with dots:
    #   ("body", "duration", "days") -> "duration.days"
    #   ("query", "unit")            -> "unit"
    # str(p) is needed because list positions in the path are integers.
    # `or None` turns an empty string into None (JSON null).
    loc = ".".join(
        str(p) for p in first.get("loc", []) if p not in ("body", "query")
    ) or None
    return JSONResponse(
        status_code=422,
        content={
            "error_code": "VALIDATION_ERROR",
            # .get(key, default) supplies a fallback message if "msg" is missing.
            "message": first.get("msg", "Invalid request body"),
            "field": loc,
        },
    )


# Catch-all: `Exception` is the parent of (almost) every error type, so this
# handles anything the two handlers above didn't. We deliberately send a
# generic message: exposing internal error details or stack traces to
# clients can leak information about how the server works.
# (In a real project you would also log `exc` here so you could debug it.)
@app.exception_handler(Exception)
async def internal_error_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={
            "error_code": "INTERNAL_ERROR",
            "message": "An unexpected error occurred",
            "field": None,
        },
    )


# ---------- Routes ----------
#
# A route connects an HTTP method + URL path to a Python function.
#   @app.get("/health")              -> handles GET  requests to /health
#   @app.post("/api/v1/dates/add")   -> handles POST requests to /api/v1/dates/add
#
# response_model=... tells FastAPI what the response looks like. FastAPI uses
# it to validate/filter what we return and to document the response in /docs.
# tags=[...] groups the routes into sections on the /docs page.
#
# GET is used for /health, the current-time lookup and the countdown because
# they only read information and need just a few small inputs (sent in the URL). The date
# math endpoints use POST because the client sends a JSON body with the input.
#
# "/api/v1/..." puts a version number in the URL. If the API ever has to change
# in a way that would break existing clients, a "/api/v2/..." can be added
# alongside it while old clients keep using v1.
#
# These are plain `def` functions (not `async def`). FastAPI runs plain
# functions in a background thread pool, which is fine for quick, CPU-only
# work like date math. `async def` is mainly useful when a route awaits slow
# I/O such as network calls or database queries.

@app.get("/health", response_model=HealthResponse, tags=["System"])
def health():
    """Liveness check: if this answers, the service is up."""
    return HealthResponse(
        status="ok",
        service="date-time-tracker",
        version=VERSION,
        # datetime.now(timezone.utc) gives the current time as an aware UTC
        # datetime. .isoformat() produces "2026-11-16T23:47:00.123456+00:00";
        # replacing "+00:00" with "Z" gives the common short form for UTC.
        utc_time=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )


@app.get("/api/v1/datetime/current", response_model=CurrentTimeResponse, tags=["DateTime"])
def current_time(
    # Query(...) tells FastAPI this value comes from the URL's query string:
    #   GET /api/v1/datetime/current?timezone=America/Los_Angeles
    #
    # alias="timezone": the parameter is called "timezone" in the URL (as the
    # team agreed in the Assignment 6 plan), but inside Python we name it
    # tz_name. Naming it `timezone` here would hide the `timezone` imported
    # from datetime at the top of this file, which is confusing and easy to
    # break later.
    #
    # default="UTC": if the client leaves the parameter out, use UTC.
    tz_name: str = Query(
        default="UTC",
        alias="timezone",
        description="IANA time zone name, e.g. America/Los_Angeles",
    ),
):
    """Return the current date and time in the requested time zone."""
    # Same validation as the other endpoints (400 if the zone is unknown),
    # but the error names this endpoint's parameter, "timezone".
    tz = parse_zone(tz_name, field="timezone")

    # get_current_time returns a dict whose keys match CurrentTimeResponse.
    # `**` unpacks the dict into keyword arguments, so this line is the same as
    # CurrentTimeResponse(timezone=tz_name, date=..., time=..., ...).
    return CurrentTimeResponse(timezone=tz_name, **get_current_time(tz))


@app.get("/api/v1/datetime/countdown", response_model=CountdownResponse, tags=["DateTime"])
def countdown(
    # `...` as the default means the parameter is required: a request without
    # ?target=... gets a 422 error.
    target: str = Query(..., description="ISO 8601 date or datetime to count down to"),
    tz_name: str = Query(
        default="UTC",
        alias="timezone",
        description="IANA time zone used for a target with no UTC offset",
    ),
    # Optional[CountdownUnit]: either left out (None) or exactly one of
    # "days", "hours", "minutes", "seconds". Anything else gets a 422 error.
    unit: Optional[CountdownUnit] = Query(
        default=None, description="Also express the time remaining in this unit"
    ),
):
    """Return the time remaining from now until `target`."""
    tz = parse_zone(tz_name, field="timezone")
    # A target with no offset (e.g. "2026-12-25") is read as local time in tz.
    target_dt = parse_dt(target, tz, "target")

    # Read the clock once and use that same moment for both the calculation
    # and the `now` field, so the response is consistent with itself.
    now = datetime.now(tz).replace(microsecond=0)
    result = compute_countdown(target_dt, now)

    value = None
    if unit is not None:
        value = countdown_in_unit(result["total_seconds"], unit)

    return CountdownResponse(
        target=target,
        timezone=tz_name,
        now=now.isoformat(),
        unit=unit,
        value=value,
        # `**result` unpacks has_passed, remaining and total_seconds.
        **result,
    )


@app.post("/api/v1/dates/add", response_model=AddSubtractResponse, tags=["Dates"])
def add_duration(req: AddSubtractRequest):
    """Add a duration to a start date/datetime."""
    # Because the parameter is typed as AddSubtractRequest, FastAPI reads the
    # JSON body, validates it with that model, and passes us the result.
    # By the time we get here, `req` is guaranteed to be valid in shape.

    # Step 1: turn the time zone name into a ZoneInfo (400 error if unknown).
    # `req.time_zone or "UTC"` falls back to UTC if the client sent null.
    tz = parse_zone(req.time_zone or "UTC")

    # Step 2: turn the start string into an aware datetime (400 error if invalid).
    start = parse_dt(req.start, tz, "start")

    # Step 3: convert the Duration model into a plain dict, e.g.
    # {"years": 0, "months": 1, "days": 0, "hours": 0, "minutes": 0, "seconds": 0}
    duration = req.duration.model_dump()

    # Step 4: do the math. The function returns two values, which we unpack
    # into two variables at once.
    result, clamped = apply_duration(start, duration, sign=1)

    # Step 5: build and return the response model; FastAPI turns it into JSON.
    return AddSubtractResponse(
        start=req.start,
        duration=duration,
        result=_format_result(result, req.start),
        clamped=clamped,
        time_zone=req.time_zone or "UTC",
    )


@app.post("/api/v1/dates/subtract", response_model=AddSubtractResponse, tags=["Dates"])
def subtract_duration(req: AddSubtractRequest):
    """Subtract a duration from a start date/datetime."""
    tz = parse_zone(req.time_zone or "UTC")
    start = parse_dt(req.start, tz, "start")

    # For the RESPONSE only, show the duration as negative numbers so the
    # client can see it was subtracted. This is a "dict comprehension": it
    # builds a new dict by looping over every key/value pair of the old one.
    #   {"months": 1, "days": 0, ...} -> {"months": -1, "days": 0, ...}
    duration = {k: -v for k, v in req.duration.model_dump().items()}

    # For the MATH, pass the original positive values and let sign=-1 flip
    # them inside apply_duration. (Passing the negative dict AND sign=-1
    # would flip twice and add instead of subtract.)
    result, clamped = apply_duration(start, req.duration.model_dump(), sign=-1)
    return AddSubtractResponse(
        start=req.start,
        duration=duration,
        result=_format_result(result, req.start),
        clamped=clamped,
        time_zone=req.time_zone or "UTC",
    )


@app.post("/api/v1/dates/difference", response_model=DifferenceResponse, tags=["Dates"])
def difference(req: DifferenceRequest):
    """Measure the gap between a start and an end date/datetime."""
    tz = parse_zone(req.time_zone or "UTC")
    # The third argument names the field, so an error can say whether it was
    # "start" or "end" that was invalid.
    start = parse_dt(req.start, tz, "start")
    end = parse_dt(req.end, tz, "end")

    # compute_difference returns a 4-item tuple; unpack it into 4 variables.
    sign, calendar, total_days, total_seconds = compute_difference(start, end)

    # If the client asked for a unit, also express the gap in that unit.
    # Otherwise `value` stays None and appears as null in the JSON.
    value = None
    if req.unit is not None:
        value = difference_in_unit(calendar, total_days, req.unit)

    return DifferenceResponse(
        start=req.start,
        end=req.end,
        sign=sign,
        # `calendar` is a plain dict here. Pydantic automatically converts it
        # into the nested CalendarBreakdown model declared in models.py.
        calendar=calendar,
        total_days=total_days,
        total_seconds=total_seconds,
        unit=req.unit,
        value=value,
    )


# The leading underscore is a Python convention meaning "private helper":
# it's used only inside this module and isn't a route or part of the API.
def _format_result(result: datetime, original_input: str) -> str:
    """Date-only input -> date-only output; datetime input -> datetime output.

    Matching the client's input style means someone who sends "2026-01-31"
    gets back "2026-02-28", not "2026-02-28T00:00:00+00:00".
    """
    # A datetime string always contains a "T" (between date and time) or a
    # ":" (inside the time). If neither is present, the input was date-only.
    is_date_only = "T" not in original_input and ":" not in original_input
    if is_date_only:
        # .date() drops the time part; .isoformat() gives "YYYY-MM-DD".
        return result.date().isoformat()
    # Full ISO 8601 datetime with offset, e.g. "2026-11-01T14:45:00-05:00".
    return result.isoformat()
