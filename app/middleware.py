"""
middleware.py - Code that runs around EVERY request.

Purpose of this file
-------------------
Acts as a bridge  between the web server and the route functions. Every
request passes through it on the way in, and every response passes through
it on the way out:

From the client:
    client --> middleware --> route function (i.e. add_duration)
To the client:
    client <-- middleware <-- route function

This middleware measures how long each request takes and reports it back to
the client in a response header called X-Process-Time-Ms. 

It is activated on in main.py with: app.add_middleware(ProcessTimeMiddleware)
"""

import time

# FastAPI is built on top of a smaller framework called Starlette.
# BaseHTTPMiddleware is Starlette's helper class for writing middleware:
# you subclass it and fill in the `dispatch` method.
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class ProcessTimeMiddleware(BaseHTTPMiddleware):
    """Adds an X-Process-Time-Ms header showing how long the request took."""

    # `async def` makes this a coroutine. FastAPI/Starlette run on an
    # asynchronous event loop, so middleware must be async.
    #
    # request   - the incoming HTTP request.
    # call_next - a function that passes the request on to the next layer
    #             (eventually your route) and gives back its response.
    async def dispatch(self, request: Request, call_next):
        # time.perf_counter() is a high-resolution clock meant for measuring
        # short intervals. Its absolute value is meaningless; only the
        # difference between two readings matters. (Don't use time.time()
        # for this: it can jump if the system clock is adjusted.)
        start = time.perf_counter()

        # Everything BEFORE this line runs on the way in.
        # `await` pauses here until the rest of the app has produced a response.
        response = await call_next(request)
        # Everything AFTER this line runs on the way out.

        # Seconds -> milliseconds.
        elapsed_ms = (time.perf_counter() - start) * 1000

        # Response headers behave like a dictionary. Header values must be
        # strings; :.3f formats the number with 3 decimal places, e.g. "0.842".
        # The "X-" prefix is a common convention for custom headers.
        response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.3f}"

        # Always return the response, or the client receives nothing.
        return response
