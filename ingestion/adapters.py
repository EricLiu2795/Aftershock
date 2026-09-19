"""Source-specific parsing behind the same fetch() interface."""

from datetime import datetime, timedelta, timezone
from html import unescape
import json
import logging
from pathlib import Path
import re
from urllib.parse import urlencode

from ingestion.http import FetchError, JsonHttpClient
from ingestion.models import NormalizedRawInput, utc_timestamp
from ingestion.validation import validator

log = logging.getLogger(__name__)


def plain_text(value):
    if not isinstance(value, str):
        raise ValueError("expected text")
    return unescape(re.sub(r"<[^>]+>", " ", value)).strip()


class LiveAdapter:
    """Failed fetches are observable, and a later poll can retry safely."""
    def __init__(self, http=None):
        self.http = http or JsonHttpClient()
        self.last_error = None

    def fetch(self):
        self.last_error = None
        try:
            url, body = self.request()
            items = self.parse(self.http.request(url, body=body))
            log.info("source_fetched source=%s count=%d", type(self).__name__, len(items))
            return items
        except (FetchError, ValueError, TypeError, KeyError, AttributeError) as error:
            self.last_error = error if isinstance(error, FetchError) else FetchError("malformed_response")
            log.warning("source_failed source=%s code=%s", type(self).__name__, self.last_error.code)
            # Retry-After is exposed on last_error, never immediately hammer 429s.
            return []

    def parse(self, payload):
        if not isinstance(payload, dict) or not isinstance(payload.get(self.collection), list):
            raise ValueError("missing provider collection")
        items = []
        for index, record in enumerate(payload[self.collection]):
            try:
                item = self.parse_record(record)
                if item is not None:
                    items.append(item)
            except (ValueError, TypeError, KeyError, AttributeError, IndexError):
                log.warning("record_skipped source=%s index=%d reason=malformed_record", type(self).__name__, index)
        return sorted(items, key=lambda item: datetime.fromisoformat(item.timestamp.replace("Z", "+00:00")))


class GDACSAdapter(LiveAdapter):
    collection = "features"

    def __init__(self, *, base_url="https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH",
                 event_types="EQ", country=None, event_id=None, since=None, until=None,
                 limit=20, page=1, http=None):
        super().__init__(http)
        if not 1 <= limit <= 100 or page < 1:
            raise ValueError("GDACS limit must be 1..100 and page >= 1")
        self.base_url, self.event_types = base_url, event_types
        self.country, self.event_id = country, event_id
        self.since = since or (datetime.now(timezone.utc) - timedelta(days=7)).date().isoformat()
        self.until = until or datetime.now(timezone.utc).date().isoformat()
        self.limit, self.page = limit, page

    def request(self):
        return self.base_url + "?" + urlencode({
            "eventlist": self.event_types, "fromdate": self.since, "todate": self.until,
            "pagesize": self.limit, "pagenumber": self.page,
        }), None

    def parse_record(self, record):
        props = record["properties"]
        country = props.get("country")
        event_id = str(props["eventid"])
        if self.country and str(country).casefold() != self.country.casefold():
            return None
        if self.event_id and event_id != str(self.event_id):
            return None
        event_type = str(props["eventtype"])
        episode = props.get("episodeid", "0")
        timestamp = utc_timestamp(props.get("fromdate") or props["todate"], assume_utc=True)
        url = (props.get("url") or {}).get("report") if isinstance(props.get("url"), dict) else props.get("url")
        url = url or "https://www.gdacs.org/report.aspx?" + urlencode({"eventtype": event_type, "eventid": event_id})
        return NormalizedRawInput(
            source_type="gdacs", source_id="gdacs",
            external_id=f"{event_type}:{event_id}:{episode}", timestamp=timestamp,
            title=plain_text(props.get("name") or props.get("eventname") or event_type),
            content=plain_text(props.get("description") or props.get("name") or event_type),
            source_url=url, raw_location_text=country,
            structured_metadata={"event_id": event_id, "event_type": event_type,
                                 "episode_id": episode, "country": country,
                                 "alert_level": props.get("alertlevel"),
                                 "event_geometry": record.get("geometry"),
                                 "geometry_scope": "event_extent_or_epicenter_not_building",
                                 "provider_updated_at": props.get("datemodified") or props.get("todate")},
            raw_payload=record,
        )


class ReliefWebAdapter(LiveAdapter):
    collection = "data"

    def __init__(self, *, appname, base_url="https://api.reliefweb.int/v2/reports",
                 query="earthquake", country=None, since=None, until=None,
                 limit=20, offset=0, http=None):
        super().__init__(http)
        if not appname:
            raise ValueError("RELIEFWEB_APPNAME must be a pre-approved appname")
        if not 1 <= limit <= 1000 or offset < 0:
            raise ValueError("invalid ReliefWeb pagination")
        self.appname, self.base_url, self.query = appname, base_url, query
        self.country, self.until = country, until
        self.since = since or utc_timestamp((datetime.now(timezone.utc) - timedelta(days=7)).isoformat())
        self.limit, self.offset = limit, offset

    def request(self):
        dates = {"from": self.since}
        if self.until:
            dates["to"] = self.until
        conditions = [{"field": "date.original", "value": dates}]
        if self.country:
            conditions.append({"field": "country.name", "value": self.country})
        body = {"limit": self.limit, "offset": self.offset, "sort": ["date.original:desc"],
                "query": {"value": self.query},
                "filter": {"operator": "AND", "conditions": conditions},
                "fields": {"include": ["title", "body", "date", "source", "url", "country", "language"]}}
        return self.base_url + "?" + urlencode({"appname": self.appname}), body

    def parse_record(self, record):
        fields = record["fields"]
        countries = fields.get("country") or []
        title = plain_text(fields["title"])
        return NormalizedRawInput(
            source_type="reliefweb", source_id="reliefweb", external_id=str(record["id"]),
            timestamp=fields["date"].get("original") or fields["date"]["created"],
            title=title, content=plain_text(fields.get("body") or title),
            source_url=fields.get("url") or record.get("href"),
            raw_location_text=", ".join(c["name"] for c in countries) or None,
            structured_metadata={"organizations": fields.get("source", []),
                                 "countries": countries, "languages": fields.get("language", []),
                                 "publication_date": fields["date"], "scope": "report_not_building"},
            raw_payload=record,
        )


class SocialReplayAdapter:
    """Each fetch advances a cursor; input records use canonical RawSocialInput."""
    def __init__(self, path: str | Path, batch_size=1):
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        records = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(records, list):
            raise ValueError("replay must be an array")
        self.items = []
        for record in records:
            validator("raw_social_input.schema.json").validate(record)
            self.items.append(NormalizedRawInput(
                source_type="ushahidi" if record["platform"] == "ushahidi" else "social",
                source_id=record["platform"], external_id=record.get("external_id") or record["input_id"],
                timestamp=record["timestamp"], content=record["text"],
                source_url=record.get("source_url"), language=record.get("language"),
                structured_metadata={"platform": record["platform"], "author_id": record.get("author_id"),
                                     "source_metadata": record.get("metadata", {})},
                raw_payload=record,
            ))
        self.items.sort(key=lambda item: datetime.fromisoformat(item.timestamp.replace("Z", "+00:00")))
        self.cursor, self.batch_size = 0, batch_size

    def fetch(self):
        items = self.items[self.cursor:self.cursor + self.batch_size]
        self.cursor += len(items)
        log.info("source_fetched source=social_replay count=%d", len(items))
        return items
