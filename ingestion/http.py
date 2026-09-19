"""Small injectable HTTP boundary; no response bodies or credentials in errors."""

import json
import math
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class FetchError(Exception):
    def __init__(self, code: str, retry_after: str | None = None):
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


class JsonHttpClient:
    def __init__(self, timeout: float = 15, max_bytes: int = 4_000_000):
        if not math.isfinite(timeout) or timeout <= 0 or max_bytes <= 0:
            raise ValueError("positive finite HTTP limits required")
        self.timeout = timeout
        self.max_bytes = max_bytes

    def request(self, url: str, *, body=None, headers=None):
        request = Request(
            url,
            data=None if body is None else json.dumps(body, allow_nan=False).encode(),
            headers={"Accept": "application/json", "User-Agent": "Aftershock-Ingestion/1.0",
                     **({"Content-Type": "application/json"} if body is not None else {}),
                     **(headers or {})},
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                if response.status != 200:
                    raise FetchError(f"http_{response.status}", response.headers.get("Retry-After"))
                data = response.read(self.max_bytes + 1)
                if len(data) > self.max_bytes:
                    raise FetchError("response_too_large")
                if not data.strip():
                    raise FetchError("empty_response")
                return json.loads(data)
        except HTTPError as error:
            retry_after = error.headers.get("Retry-After") if error.headers else None
            error.close()
            raise FetchError(f"http_{error.code}", retry_after) from None
        except (URLError, TimeoutError, OSError, HTTPException):
            raise FetchError("connection_or_timeout") from None
        except (ValueError, UnicodeError):
            raise FetchError("malformed_json") from None
