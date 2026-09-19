"""Provider choices are isolated here; the extraction pipeline is provider-neutral."""

from dataclasses import asdict
import json
from pathlib import Path

from ingestion.http import JsonHttpClient
from ingestion.normalizer import ExtractionRequest


class OpenAIExtractionModel:
    def __init__(self, *, api_key, model, base_url="https://api.openai.com/v1", http=None):
        if not api_key or not model:
            raise ValueError("OPENAI_API_KEY and INGESTION_AI_MODEL are required")
        self.api_key, self.model, self.base_url = api_key, model, base_url.rstrip("/")
        self.http = http or JsonHttpClient(timeout=45)

    def extract(self, request: ExtractionRequest) -> str:
        raw = asdict(request.raw)
        raw.pop("raw_payload")  # Retain locally; do not send redundant large API data.
        source = {"source": raw, "envelope": request.envelope,
                  "repair_feedback": request.repair_feedback}
        response = self.http.request(
            self.base_url + "/responses",
            headers={"Authorization": f"Bearer {self.api_key}"},
            body={"model": self.model, "store": False, "instructions": request.system_prompt,
                  "input": json.dumps(source, ensure_ascii=False, allow_nan=False),
                  "max_output_tokens": 6000,
                  "text": {"format": {"type": "json_schema", "name": "aftershock_observations",
                                      "strict": True, "schema": request.schema}}},
        )
        if not isinstance(response, dict) or response.get("status") != "completed":
            raise ValueError("incomplete_provider_response")
        texts = []
        for item in response.get("output", []):
            if item.get("type") == "message":
                for content in item.get("content", []):
                    if content.get("type") == "refusal":
                        raise ValueError("provider_refusal")
                    if content.get("type") == "output_text":
                        texts.append(content["text"])
        if not texts:
            raise ValueError("empty_provider_output")
        return "".join(texts)


class FixtureExtractionModel:
    """Explicit offline mock: curated answers for exact fixture inputs, no NLP."""
    def __init__(self, path: str | Path):
        self.responses = json.loads(Path(path).read_text(encoding="utf-8"))

    def extract(self, request: ExtractionRequest) -> str:
        key = f"{request.raw.source_type}:{request.raw.source_id}:{request.raw.external_id}"
        entry = self.responses[key]
        if entry["content"] != request.raw.content:
            raise ValueError("fixture_content_mismatch")
        # Fixture placeholders carry deterministic source identity, just as the
        # live model is instructed to do. Fact fields are hand-authored mock output.
        envelope = request.envelope
        observations = []
        for index, facts in enumerate(entry["observations"]):
            observation = {k: v for k, v in envelope.items() if k != "observation_id_prefix"}
            observation.update(facts)
            observation["observation_id"] = envelope["observation_id_prefix"] + str(index)
            observations.append(observation)
        return json.dumps({"observations": observations})
