"""
demo_client.py - Test program for the Date and Time Tracker microservice.

This program shows how another program (such as a teammate's main program)
REQUESTS data from the microservice and RECEIVES the response. It calls every
endpoint once and prints exactly what was sent and what came back.

HOW TO RUN
----------
1. In one terminal, start the microservice (from the project folder): 
    uvicorn app.main:app --port 8000
    
2. In a second terminal, run this program: 
    python demo_client.py

It uses only Python's standard library, so nothing extra has to be installed.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

# The address of the microservice, as given in the communication contract.
BASE_URL = "http://localhost:8000"


# ---------------------------------------------------------------------------
# Two small helpers: one for GET requests and one for POST requests.
# Each one REQUESTS data, then RECEIVES and decodes the JSON response.
# ---------------------------------------------------------------------------

def get(path, params=None):
    """Send a GET request. Any input goes in the URL's query string."""
    url = BASE_URL + path
    if params:
        # urlencode turns {"timezone": "America/Los_Angeles"} into
        # "timezone=America%2FLos_Angeles" (the "/" is safely encoded).
        url += "?" + urllib.parse.urlencode(params)
    print(f"REQUEST:  GET {url}")
    return _send(urllib.request.Request(url, method="GET"))


def post(path, body):
    """Send a POST request. The input goes in the body as JSON."""
    url = BASE_URL + path
    data = json.dumps(body).encode("utf-8")   # Python dict -> JSON text -> bytes
    print(f"REQUEST:  POST {url}")
    print(f"          body: {json.dumps(body)}")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        # Tell the microservice the body is JSON.
        headers={"Content-Type": "application/json"},
    )
    return _send(request)


def _send(request):
    """Send the request, then receive the response and turn its JSON into a dict."""
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            status = response.status
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        # 4xx/5xx responses raise HTTPError, but they still carry the
        # microservice's JSON error body, so read it the same way.
        status = error.code
        data = json.loads(error.read().decode("utf-8"))
    print(f"RESPONSE: HTTP {status}")
    print("          " + json.dumps(data, indent=2).replace("\n", "\n          "))
    return status, data


def section(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


# ---------------------------------------------------------------------------
# The demonstration: one call to each endpoint.
# ---------------------------------------------------------------------------

def main():
    section("1. Health check: is the microservice running?")
    get("/health")

    section("2. Current date and time in a specific time zone")
    status, data = get("/api/v1/datetime/current", {"timezone": "America/Los_Angeles"})
    if status == 200:
        # Using the received data, the way a main program would:
        print(f"\nReceived -> Date: {data['date']}   Time: {data['time']}   "
              f"(UTC offset {data['utc_offset']})")

    section("3. Current date and time with no time zone (defaults to UTC)")
    get("/api/v1/datetime/current")

    section("4. Countdown to a future date")
    status, data = get("/api/v1/datetime/countdown", {
        "target": "2026-12-25",
        "timezone": "America/Los_Angeles",
    })
    if status == 200:
        rem = data["remaining"]
        print(f"\nReceived -> {rem['days']} days, {rem['hours']} hours, "
              f"{rem['minutes']} minutes, {rem['seconds']} seconds left")

    section("5. Countdown in a single unit (total hours remaining)")
    status, data = get("/api/v1/datetime/countdown", {
        "target": "2026-12-25T00:00:00",
        "timezone": "America/Los_Angeles",
        "unit": "hours",
    })
    if status == 200:
        print(f"\nReceived -> {data['value']} {data['unit']} left")

    section("6. Add a duration to a date (Jan 31 + 1 month)")
    status, data = post("/api/v1/dates/add", {
        "start": "2026-01-31",
        "duration": {"months": 1},
    })
    if status == 200:
        print(f"\nReceived -> Result: {data['result']}   Clamped: {data['clamped']}")

    section("7. Subtract a duration from a datetime in a time zone")
    post("/api/v1/dates/subtract", {
        "start": "2026-11-16T09:00:00",
        "duration": {"days": 10, "hours": 2},
        "time_zone": "America/Los_Angeles",
    })

    section("8. Difference between two dates")
    status, data = post("/api/v1/dates/difference", {
        "start": "2024-02-29",
        "end": "2026-03-01",
    })
    if status == 200:
        cal = data["calendar"]
        print(f"\nReceived -> {cal['years']} years, {cal['months']} months, "
              f"{cal['days']} days  ({data['total_days']} days in total)")

    section("9. Difference in a chosen unit (weeks)")
    status, data = post("/api/v1/dates/difference", {
        "start": "2024-02-29",
        "end": "2026-03-01",
        "unit": "weeks",
    })
    if status == 200:
        print(f"\nReceived -> {data['value']} {data['unit']}")

    section("10. Error handling: an invalid time zone")
    get("/api/v1/datetime/current", {"timezone": "Mars/Olympus_Mons"})

    section("11. Error handling: an invalid date")
    post("/api/v1/dates/add", {"start": "not-a-date", "duration": {"days": 1}})

    print("\nDemo complete.")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.URLError:
        print("\nCould not connect to the microservice at " + BASE_URL)
        print("Start it first with:  uvicorn app.main:app --port 8000")
