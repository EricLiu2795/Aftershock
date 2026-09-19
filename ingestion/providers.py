"""Provider choices are isolated here; the extraction pipeline is provider-neutral."""

import json
from pathlib import Path

from ingestion.http import FetchError, JsonHttpClient
from ingestion.normalizer import ExtractionRequest

_IDENTITY_FIELDS = {
    "schema_version", "observation_id", "timestamp", "source_type", "source_id",
    "verification_status", "raw_source_reference", "metadata", "evidence_strength",
}

_FETCH_REASONS = {
    "http_401": "invalid_api_key",
    "http_403": "invalid_api_key",
    "connection_or_timeout": "provider_timeout",
    "http_408": "provider_timeout",
    "http_504": "provider_timeout",
}


def model_request_payload(request: ExtractionRequest) -> dict:
    """CONTENT vs SOURCE METADATA for the model; never includes raw_payload."""
    raw = request.raw
    payload = {
        "content": {
            "title": raw.title,
            "text": raw.content,
            "language": raw.language,
            "raw_location_text": raw.raw_location_text,
        },
        "source_metadata": {
            "source_type": raw.source_type,
            "source_id": raw.source_id,
            "external_id": raw.external_id,
            "timestamp": raw.timestamp,
            "source_url": raw.source_url,
            "language": raw.language,
            "raw_location_text": raw.raw_location_text,
            "provider_metadata": raw.structured_metadata,
        },
    }
    if request.repair_feedback:
        payload["repair_feedback"] = request.repair_feedback
    return payload


def _json_fragment(value) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    return None


def structured_output_text(response) -> str:
    if not isinstance(response, dict):
        raise ValueError("malformed_provider_result")
    if response.get("error"):
        raise ValueError("provider_failure")
    if response.get("status") != "completed":
        raise ValueError("incomplete_provider_response")
    texts = []
    for item in response.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if not isinstance(content, dict):
                continue
            if content.get("type") == "refusal" or content.get("refusal"):
                raise ValueError("provider_refusal")
            fragment = _json_fragment(content.get("text"))
            if content.get("type") in {"output_text", "text"} and fragment:
                texts.append(fragment)
                continue
            fragment = _json_fragment(content.get("parsed", content.get("json")))
            if fragment:
                texts.append(fragment)
    if texts:
        return "".join(texts)
    for key in ("output_text", "output_parsed"):
        fragment = _json_fragment(response.get(key))
        if fragment:
            return fragment
    raise ValueError("empty_provider_output")


class OpenAIExtractionModel:
    """Official OpenAI Responses API + Structured Outputs. No SDK dependency."""

    def __init__(self, *, api_key, model, base_url="https://api.openai.com/v1", http=None):
        if not str(api_key or "").strip() or not str(model or "").strip():
            raise ValueError("OPENAI_API_KEY and INGESTION_AI_MODEL are required")
        self.api_key, self.model, self.base_url = api_key, model, base_url.rstrip("/")
        self.http = http or JsonHttpClient(timeout=45)

    def extract(self, request: ExtractionRequest) -> str:
        try:
            response = self.http.request(
                self.base_url + "/responses",
                headers={"Authorization": f"Bearer {self.api_key}"},
                body={
                    "model": self.model,
                    "store": False,
                    "instructions": request.system_prompt,
                    "input": json.dumps(
                        model_request_payload(request), ensure_ascii=False, allow_nan=False
                    ),
                    "max_output_tokens": 6000,
                    "text": {
                        "format": {
                            "type": "json_schema",
                            "name": "aftershock_observations",
                            "strict": True,
                            "schema": request.schema,
                        }
                    },
                },
            )
        except FetchError as error:
            raise ValueError(_FETCH_REASONS.get(error.code, error.code)) from None
        return structured_output_text(response)


class FixtureExtractionModel:
    """Explicit offline mock: curated answers for exact fixture inputs, no NLP."""

    def __init__(self, path: str | Path):
        self.responses = json.loads(Path(path).read_text(encoding="utf-8"))

    def extract(self, request: ExtractionRequest) -> str:
        key = f"{request.raw.source_type}:{request.raw.source_id}:{request.raw.external_id}"
        entry = self.responses[key]
        if entry["content"] != request.raw.content:
            raise ValueError("fixture_content_mismatch")
        observations = [
            {k: v for k, v in facts.items() if k not in _IDENTITY_FIELDS}
            for facts in entry["observations"]
        ]
        return json.dumps({"observations": observations})
