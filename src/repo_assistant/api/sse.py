"""Server-sent events formatting.

Hand-rolled rather than a dependency: the SSE wire format is three lines. Every
event is a named event with a JSON body, so clients switch on ``event`` and
always ``JSON.parse`` the data.
"""

import json
from typing import Any

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    # Tell nginx-style proxies not to buffer the stream.
    "X-Accel-Buffering": "no",
}
SSE_MEDIA_TYPE = "text/event-stream"


def sse_event(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
