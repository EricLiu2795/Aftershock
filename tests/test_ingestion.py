"""Ingestion behavior tests: all API/model responses mocked; no network required."""

from copy import deepcopy
from dataclasses import replace
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from jsonschema import ValidationError

from ingestion.adapters import GDACSAdapter, ReliefWebAdapter, SocialReplayAdapter
from ingestion.config import Config, load_dotenv
from ingestion.demo import SMOKE_FILE, main
from ingestion.http import FetchError, JsonHttpClient
from ingestion.models import NormalizedRawInput
from ingestion.normalizer import AIInformationNormalizer, IngestionPipeline, NormalizationError
from ingestion.providers import FixtureExtractionModel, OpenAIExtractionModel
from ingestion.validation import extraction_schema, validate_structured_observation

ROOT = Path(__file__).resolve().parents[1]


def fixture(name):
    return json.loads((ROOT / "tests/fixtures" / name).read_text(encoding="utf-8"))


class IngestionTests(unittest.TestCase):
    def setUp(self):
        self.replay = SocialReplayAdapter(ROOT / "tests/fixtures/social_replay_mixed.json")
        self.model = FixtureExtractionModel(ROOT / "data/ingestion_ai_responses.json")
        self.normalizer = AIInformationNormalizer(self.model)
        self.raw = self.replay.items[0]

    def response_model(self, mutate=None):
        def extract(request):
            result = json.loads(self.model.extract(request))
            if mutate:
                mutate(result)
            return json.dumps(result)
        return Mock(extract=Mock(side_effect=extract))

    def test_replay_parsing_and_incremental_order(self):
        items = []
        while batch := self.replay.fetch():
            self.assertEqual(len(batch), 1)
            items.extend(batch)
        self.assertEqual(len(items), 10)
        self.assertEqual([x.timestamp for x in items], sorted(x.timestamp for x in items))
        self.assertEqual(self.replay.fetch(), [])

    def test_replay_sorts_unsorted_fractional_and_timezone_timestamps(self):
        records = deepcopy([x.raw_payload for x in self.replay.items[:3]])
        records[0]["timestamp"] = "2026-09-19T15:00:00.5+01:00"
        records[1]["timestamp"] = "2026-09-19T14:00:00Z"
        records[2]["timestamp"] = "2026-09-19T13:59:00Z"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "replay.json"
            path.write_text(json.dumps(records), encoding="utf-8")
            replay = SocialReplayAdapter(path, batch_size=3)
            self.assertEqual([x.external_id for x in replay.fetch()], ["post-003", "post-002", "post-001"])

    def test_replay_rejects_invalid_contract_record(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text('[{"schema_version":"1.0"}]', encoding="utf-8")
            with self.assertRaises(ValidationError):
                SocialReplayAdapter(path)

    def test_normalized_raw_preserves_source(self):
        self.assertIsInstance(self.raw, NormalizedRawInput)
        self.assertEqual(self.raw.source_type, "social")
        self.assertEqual(self.raw.external_id, "post-001")
        self.assertEqual(self.raw.language, "en")
        self.assertEqual(self.raw.raw_payload["text"], self.raw.content)
        self.assertIn("author_id", self.raw.structured_metadata)

    def test_invalid_raw_timestamp_and_content_rejected(self):
        for changes in ({"timestamp": "yesterday"}, {"timestamp": "2026-09-19T12:00:00"}, {"content": ""}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(self.raw, **changes)

    def test_current_contract_validation_and_v1_rejection(self):
        observation = self.normalizer.normalize(self.raw)[0]
        validate_structured_observation(observation)
        observation["schema_version"] = "1.0"
        with self.assertRaises(ValidationError):
            validate_structured_observation(observation)

    def test_unknown_fields_not_fabricated(self):
        observation = self.normalizer.normalize(self.raw)[0]
        self.assertEqual(observation["victim_indication"], {
            "type": "possible_multiple", "count_min": None, "count_max": None})
        self.assertEqual(observation["structural_hazard"], "unknown")
        self.assertEqual(observation["access_status"], "unknown")
        self.assertEqual(observation["verification_status"], "unverified")

    def test_absent_victim_information_is_unknown_not_none(self):
        for external_id in ("post-002", "post-007"):
            raw = next(item for item in self.replay.items if item.external_id == external_id)
            for observation in self.normalizer.normalize(raw):
                with self.subTest(external_id=external_id, incident=observation["incident_type"]):
                    self.assertIn(observation["incident_type"], {"access_blocked", "structural_damage"})
                    self.assertEqual(observation["victim_indication"]["type"], "unknown")
                    self.assertIsNone(observation["victim_indication"]["count_min"])
                    self.assertIsNone(observation["victim_indication"]["count_max"])

    def test_explicit_no_victims_may_use_none(self):
        raw = replace(
            self.raw,
            content="Building collapsed near Plaza Bolivar. No one is inside.",
            external_id="post-none",
        )

        def extract(request):
            return json.dumps({"observations": [{
                "location": {"latitude": None, "longitude": None, "raw_text": "Plaza Bolivar", "accuracy": "unknown"},
                "incident_type": "structural_damage",
                "victim_indication": {"type": "none", "count_min": None, "count_max": None},
                "structural_hazard": "severe_collapse",
                "access_status": "unknown",
            }]})

        observation = AIInformationNormalizer(Mock(extract=extract)).normalize(raw)[0]
        self.assertEqual(observation["victim_indication"]["type"], "none")

    def test_vague_location_retained(self):
        raw = next(x for x in self.replay.items if x.external_id == "post-006")
        location = self.normalizer.normalize(raw)[0]["location"]
        self.assertEqual(location["raw_text"], "somewhere uphill")
        self.assertIsNone(location["latitude"])
        self.assertIsNone(location["longitude"])
        self.assertNotIn("resolved_site_id", location)

    def test_external_id_duplicate_skipped_near_duplicate_kept(self):
        model = self.response_model()
        pipeline = IngestionPipeline(AIInformationNormalizer(model))
        output = [ob for raw in self.replay.items for ob in pipeline.ingest(raw)]
        self.assertEqual(len(output), 9)
        self.assertEqual(model.extract.call_count, 9)
        self.assertEqual(len({ob["observation_id"] for ob in output}), 9)
        refs = [ob["raw_source_reference"]["value"] for ob in output]
        self.assertIn("post-001", refs)
        self.assertIn("post-005", refs)

    def test_identity_namespaces_sources(self):
        self.assertNotEqual(self.raw.identity, replace(self.raw, source_id="mastodon").identity)
        self.assertNotEqual(self.raw.identity, replace(self.raw, source_type="radio").identity)

    def test_idless_input_exact_envelope_only(self):
        raw = replace(self.raw, external_id=None)
        self.assertEqual(raw.identity, replace(raw).identity)
        self.assertNotEqual(raw.identity, replace(raw, content=raw.content + " Please help.").identity)

    def test_idless_identity_preserves_distinct_location_metadata(self):
        raw = replace(self.raw, external_id=None)
        self.assertNotEqual(raw.identity, replace(raw, raw_location_text="somewhere uphill").identity)
        self.assertNotEqual(raw.identity, replace(raw, structured_metadata={"country": "Venezuela"}).identity)
        first = replace(raw, structured_metadata={"platform": "replay", "country": "Venezuela"})
        second = replace(raw, structured_metadata={"country": "Venezuela", "platform": "replay"})
        self.assertEqual(first.identity, second.identity)

    def test_invalid_ai_output_rejected_after_one_repair(self):
        model = Mock(extract=Mock(return_value='{"observations":[{}]}'))
        pipeline = IngestionPipeline(AIInformationNormalizer(model))
        with self.assertRaises(NormalizationError):
            pipeline.ingest(self.raw)
        self.assertEqual(model.extract.call_count, 2)
        self.assertIsNotNone(model.extract.call_args.args[0].repair_feedback)
        self.assertEqual(pipeline.seen, set())

    def test_invalid_json_repaired_once(self):
        calls = []
        def extract(request):
            calls.append(request)
            return "bad JSON" if len(calls) == 1 else self.model.extract(request)
        normalizer = AIInformationNormalizer(Mock(extract=extract))
        self.assertEqual(len(normalizer.normalize(self.raw)), 1)
        self.assertEqual(len(calls), 2)

    def test_provider_failure_does_not_consume_input_or_leak_secret(self):
        model = Mock(extract=Mock(side_effect=RuntimeError("sk-secret-do-not-log")))
        pipeline = IngestionPipeline(AIInformationNormalizer(model))
        with self.assertLogs("ingestion", level="INFO") as logs:
            with self.assertRaises(NormalizationError):
                pipeline.ingest(self.raw)
        self.assertNotIn("sk-secret", " ".join(logs.output))
        self.assertFalse(pipeline.seen)
        model.extract.side_effect = self.model.extract
        self.assertEqual(len(pipeline.ingest(self.raw)), 1)

    def test_operational_fields_coordinates_and_identity_rejected(self):
        for field, value in [("save_priority", 0.9), ("search_priority", 1.3),
                             ("worksite_id", "D3"), ("site_id", "site-d3"),
                             ("recommended_resource_id", "rescue-bravo"), ("metadata", {"priority": 1})]:
            with self.subTest(field=field):
                model = self.response_model(lambda result: result["observations"][0].update({field: value}))
                with self.assertRaises(NormalizationError):
                    AIInformationNormalizer(model).normalize(self.raw)
        for field, value in [("latitude", 10.60), ("longitude", -66.84), ("resolved_site_id", "site-d3")]:
            with self.subTest(field=field):
                model = self.response_model(lambda result: result["observations"][0]["location"].update({field: value}))
                with self.assertRaises(NormalizationError):
                    AIInformationNormalizer(model).normalize(self.raw)

    def test_fabricated_location_and_count_rejected(self):
        mutations = [
            lambda r: r["observations"][0]["location"].update(raw_text="1842 Imaginary Street"),
            lambda r: r["observations"][0]["victim_indication"].update(count_min=8, count_max=8),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate), self.assertRaises(NormalizationError):
                AIInformationNormalizer(self.response_model(mutate)).normalize(self.raw)

    def test_explicit_count_allowed(self):
        raw = replace(self.raw, content="3 people trapped near Riverside Apartments.")
        base_model = self.response_model()
        # Build a complete valid mock response using the same envelope as the source.
        def extract(request):
            fixture_request = replace(request, raw=self.raw)
            result = json.loads(base_model.extract(fixture_request))
            result["observations"][0]["victim_indication"].update(count_min=3, count_max=3)
            return json.dumps(result)
        observation = AIInformationNormalizer(Mock(extract=extract)).normalize(raw)[0]
        self.assertEqual(observation["victim_indication"]["count_min"], 3)

    def test_source_envelope_and_verification_tampering_rejected(self):
        for field, value in [("source_type", "radio"), ("source_id", "trusted-responder"),
                             ("timestamp", "2026-09-19T15:00:00Z"),
                             ("observation_id", "invented"), ("verification_status", "verified")]:
            with self.subTest(field=field), self.assertRaises(NormalizationError):
                model = self.response_model(lambda result: result["observations"][0].update({field: value}))
                AIInformationNormalizer(model).normalize(self.raw)

    def test_multiple_observations_and_atomic_rejection(self):
        raw = next(x for x in self.replay.items if x.external_id == "post-007")
        observations = self.normalizer.normalize(raw)
        self.assertEqual({ob["incident_type"] for ob in observations}, {"structural_damage", "access_blocked"})
        model = self.response_model(lambda r: r["observations"][1].update(incident_type="invalid"))
        with self.assertRaises(NormalizationError):
            AIInformationNormalizer(model).normalize(raw)

    def test_irrelevant_post_produces_zero_observations(self):
        raw = next(x for x in self.replay.items if x.external_id == "post-004")
        self.assertEqual(self.normalizer.normalize(raw), [])

    def test_nan_rejected_by_public_validator(self):
        observation = self.normalizer.normalize(self.raw)[0]
        observation["location"]["latitude"] = float("nan")
        with self.assertRaises(ValueError):
            validate_structured_observation(observation)

    def test_provider_schema_uses_contract_enums(self):
        schema = extraction_schema()
        from ingestion.validation import schema_documents
        incident_enum = schema_documents()["common.schema.json"]["$defs"]["incidentType"]["enum"]
        self.assertEqual(schema["properties"]["observations"]["items"]["properties"]["incident_type"]["enum"], incident_enum)

    def test_debug_logs_omit_raw_payload(self):
        with patch.dict("os.environ", {"INGESTION_DEBUG": "1"}), self.assertLogs("ingestion.normalizer", level="INFO") as logs:
            self.normalizer.normalize(self.raw)
        text = " ".join(logs.output)
        self.assertIn("debug_raw", text)
        self.assertIn("debug_validated", text)
        self.assertNotIn('"raw_payload"', text)
        self.assertNotIn("sk-", text)


class AdapterTests(unittest.TestCase):
    def test_gdacs_record_from_successful_live_verification(self):
        payload = fixture("gdacs_live_2023_response.json")
        http = Mock(request=Mock(return_value=payload))
        raw = GDACSAdapter(http=http, since="2023-02-06", until="2023-02-08", limit=1).fetch()[0]
        self.assertIsInstance(raw, NormalizedRawInput)
        self.assertEqual(raw.external_id, "EQ:1357591:1487359")
        self.assertEqual(raw.timestamp, "2023-02-06T12:02:11Z")
        self.assertEqual(raw.raw_location_text, "T\u00fcrkiye")
        self.assertEqual(raw.structured_metadata["alert_level"], "Orange")
        self.assertEqual(raw.structured_metadata["event_geometry"]["coordinates"], [36.537, 38.0605])
        self.assertIn("eventid=1357591", raw.source_url)
        self.assertEqual(raw.raw_payload, payload["features"][0])

    def test_gdacs_real_request_and_geojson_mapping(self):
        http = Mock(request=Mock(return_value=fixture("gdacs_response.json")))
        adapter = GDACSAdapter(http=http, since="2026-09-18", until="2026-09-19", country="Venezuela", event_id="1234567")
        items = adapter.fetch()
        self.assertEqual(len(items), 1)
        raw = items[0]
        self.assertEqual(raw.source_type, "gdacs")
        self.assertEqual(raw.external_id, "EQ:1234567:1")
        self.assertEqual(raw.timestamp, "2026-09-19T14:00:00Z")
        self.assertEqual(raw.structured_metadata["alert_level"], "Orange")
        self.assertEqual(raw.raw_payload["geometry"]["coordinates"], [-66.85, 10.61])
        self.assertNotIn("save_priority", raw.structured_metadata)
        query = parse_qs(urlsplit(http.request.call_args.args[0]).query)
        self.assertEqual(query["eventlist"], ["EQ"])
        self.assertEqual(query["fromdate"], ["2026-09-18"])
        self.assertEqual(query["pagenumber"], ["1"])

    def test_gdacs_country_and_event_filter(self):
        for filters in ({"country": "Nepal"}, {"event_id": "other"}):
            adapter = GDACSAdapter(http=Mock(request=Mock(return_value=fixture("gdacs_response.json"))), **filters)
            self.assertEqual(adapter.fetch(), [])

    def test_reliefweb_post_filters_and_provenance(self):
        http = Mock(request=Mock(return_value=fixture("reliefweb_response.json")))
        adapter = ReliefWebAdapter(appname="test-app", http=http, country="Venezuela", since="2026-09-18T00:00:00Z")
        raw = adapter.fetch()[0]
        self.assertEqual(raw.source_type, "reliefweb")
        self.assertIn("Collapsed buildings", raw.content)
        self.assertNotIn("<p>", raw.content)
        self.assertEqual(raw.title, "Venezuela earthquake: fictional situation report")
        self.assertEqual(raw.timestamp, "2026-09-19T14:15:00Z")
        self.assertEqual(raw.structured_metadata["organizations"][0]["name"], "Demo field coordination team")
        self.assertEqual(raw.raw_location_text, "Venezuela")
        self.assertEqual(raw.source_url, "https://example.invalid/reports/demo-123456")
        self.assertIn("appname=test-app", http.request.call_args.args[0])
        body = http.request.call_args.kwargs["body"]
        self.assertEqual(body["query"]["value"], "earthquake")
        self.assertIn("body", body["fields"]["include"])
        self.assertIn({"field": "country.name", "value": "Venezuela"}, body["filter"]["conditions"])

    def test_missing_reliefweb_appname_rejected(self):
        with self.assertRaises(ValueError):
            ReliefWebAdapter(appname="")

    def test_both_adapters_handle_empty_and_malformed_responses(self):
        for kind, collection, kwargs in [(GDACSAdapter, "features", {}), (ReliefWebAdapter, "data", {"appname": "test"})]:
            for payload in ({collection: []}, {"oops": []}, None, {collection: "bad"}):
                with self.subTest(kind=kind, payload=payload):
                    adapter = kind(http=Mock(request=Mock(return_value=payload)), **kwargs)
                    self.assertEqual(adapter.fetch(), [])
                    self.assertEqual(adapter.last_error is None, payload == {collection: []})

    def test_malformed_record_does_not_drop_good_record(self):
        for kind, name, collection, kwargs in [(GDACSAdapter, "gdacs_response.json", "features", {}),
                                               (ReliefWebAdapter, "reliefweb_response.json", "data", {"appname": "test"})]:
            payload = fixture(name)
            payload[collection].insert(0, {"bad": True})
            adapter = kind(http=Mock(request=Mock(return_value=payload)), **kwargs)
            self.assertEqual(len(adapter.fetch()), 1)

    def test_external_error_isolation_and_later_recovery(self):
        http = Mock(request=Mock(side_effect=[FetchError("http_429", "60"), fixture("gdacs_response.json")]))
        adapter = GDACSAdapter(http=http)
        self.assertEqual(adapter.fetch(), [])
        self.assertEqual(adapter.last_error.retry_after, "60")
        self.assertEqual(len(adapter.fetch()), 1)
        self.assertIsNone(adapter.last_error)


class HttpAndProviderTests(unittest.TestCase):
    def test_transport_failures_are_sanitized(self):
        errors = [TimeoutError(), URLError("secret URL"),
                  HTTPError("secret URL", 429, "rate limited", {"Retry-After": "60"}, BytesIO())]
        for error in errors:
            with self.subTest(error=type(error)), patch("ingestion.http.urlopen", side_effect=error):
                with self.assertRaises(FetchError) as caught:
                    JsonHttpClient().request("https://example.invalid")
                self.assertNotIn("secret", str(caught.exception))

    def test_transport_timeout_and_json_body(self):
        response = Mock()
        response.status = 200
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"data":[]}'
        with patch("ingestion.http.urlopen", return_value=response) as opened:
            self.assertEqual(JsonHttpClient(timeout=7).request("https://example.invalid", body={"limit": 2}), {"data": []})
        self.assertEqual(opened.call_args.kwargs["timeout"], 7)
        self.assertEqual(json.loads(opened.call_args.args[0].data), {"limit": 2})

    def test_empty_malformed_and_oversize_http_data(self):
        for data in (b"", b"<html>error</html>", b" " * 100):
            response = Mock()
            response.status = 200
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.read.return_value = data
            with patch("ingestion.http.urlopen", return_value=response), self.assertRaises(FetchError):
                JsonHttpClient(max_bytes=50).request("https://example.invalid")

    def test_non_200_response_never_parsed_as_success(self):
        for status in (202, 204, 206):
            response = Mock(status=status, headers={"Retry-After": "60"})
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.read.return_value = b'{"features":[]}'
            with self.subTest(status=status), patch("ingestion.http.urlopen", return_value=response):
                with self.assertRaises(FetchError) as caught:
                    JsonHttpClient().request("https://example.invalid")
                self.assertEqual(caught.exception.code, f"http_{status}")
                self.assertEqual(caught.exception.retry_after, "60")
                response.read.assert_not_called()

    def test_openai_native_schema_request_and_response(self):
        http = Mock(request=Mock(return_value={"status": "completed", "output": [
            {"type": "reasoning"}, {"type": "message", "content": [{"type": "output_text", "text": '{"observations":[]}'}]}]}))
        model = OpenAIExtractionModel(api_key="test-only", model="configured-model", http=http)
        raw = GDACSAdapter().parse(fixture("gdacs_response.json"))[0]
        self.assertEqual(AIInformationNormalizer(model).normalize(raw), [])
        url, kwargs = http.request.call_args.args[0], http.request.call_args.kwargs
        self.assertTrue(url.endswith("/responses"))
        payload = json.loads(kwargs["body"]["input"])
        self.assertEqual(set(payload), {"content", "source_metadata"})
        self.assertEqual(payload["content"]["text"], raw.content)
        self.assertEqual(payload["source_metadata"]["source_type"], "gdacs")
        self.assertIn("alert_level", payload["source_metadata"]["provider_metadata"])
        self.assertNotIn("raw_payload", json.dumps(payload))
        body = kwargs["body"]
        self.assertEqual(body["text"]["format"]["type"], "json_schema")
        self.assertTrue(body["text"]["format"]["strict"])
        self.assertFalse(body["store"])
        self.assertEqual(body["model"], "configured-model")
        self.assertNotIn("observation_id", body["text"]["format"]["schema"]["properties"]["observations"]["items"]["properties"])
        self.assertTrue(kwargs["headers"]["Authorization"].startswith("Bearer "))

    def test_openai_output_text_and_parsed_shapes(self):
        raw = GDACSAdapter().parse(fixture("gdacs_response.json"))[0]
        responses = [
            {"status": "completed", "output": [], "output_text": '{"observations":[]}'},
            {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "parsed": {"observations": []}}]}]},
            {"status": "completed", "error": None, "output": [{"type": "message", "content": [{"type": "text", "text": '{"observations":[]}'}]}]},
        ]
        for response in responses:
            with self.subTest(response=response):
                model = OpenAIExtractionModel(
                    api_key="test-only", model="configured-model",
                    http=Mock(request=Mock(return_value=response)),
                )
                self.assertEqual(AIInformationNormalizer(model).normalize(raw), [])

    def test_invalid_api_key_is_a_clear_failure(self):
        raw = GDACSAdapter().parse(fixture("gdacs_response.json"))[0]
        model = OpenAIExtractionModel(
            api_key="test-only", model="configured-model",
            http=Mock(request=Mock(side_effect=FetchError("http_401"))),
        )
        with self.assertRaises(NormalizationError) as caught:
            AIInformationNormalizer(model).normalize(raw)
        self.assertEqual(str(caught.exception), "invalid_api_key")

    def test_openai_model_requires_key_and_model(self):
        with self.assertRaises(ValueError):
            OpenAIExtractionModel(api_key="", model="configured-model")
        with self.assertRaises(ValueError):
            OpenAIExtractionModel(api_key="test-only", model=" ")

    def test_malformed_provider_envelope_rejected(self):
        raw = GDACSAdapter().parse(fixture("gdacs_response.json"))[0]
        for output in ("[]", "{}", '{"observations": null}', "null"):
            with self.subTest(output=output), self.assertRaises(NormalizationError):
                AIInformationNormalizer(Mock(extract=Mock(return_value=output))).normalize(raw)

    def test_provider_incomplete_refusal_and_empty_are_failures(self):
        for response in [{"status": "incomplete"}, {"status": "completed", "output": []},
                         {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal"}]}]}]:
            model = OpenAIExtractionModel(api_key="test-only", model="configured-model", http=Mock(request=Mock(return_value=response)))
            raw = GDACSAdapter().parse(fixture("gdacs_response.json"))[0]
            with self.assertRaises(NormalizationError):
                AIInformationNormalizer(model).normalize(raw)

    def test_offline_cli_emits_only_valid_jsonl(self):
        output = StringIO()
        with patch.dict("os.environ", {}, clear=True), patch("ingestion.demo.load_dotenv"), \
                patch("sys.stdout", output), patch("logging.basicConfig"), \
                patch("socket.socket.connect", side_effect=AssertionError("offline demo attempted network")):
            status = main([])
        self.assertEqual(status, 0)
        observations = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(observations), 8)
        for observation in observations:
            validate_structured_observation(observation)

    def test_env_config_no_secret_in_repr(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "secret-value", "INGESTION_MODE": "replay"}, clear=True):
            config = Config.from_env()
        self.assertNotIn("secret-value", repr(config))
        self.assertEqual(config.mode, "replay")

    def test_openai_cli_without_credentials_does_not_call_network(self):
        with patch.dict("os.environ", {}, clear=True), patch("ingestion.demo.load_dotenv"), \
                patch("sys.stdout", StringIO()), patch("logging.basicConfig"), \
                patch("ingestion.demo.OpenAIExtractionModel", side_effect=AssertionError("constructed")), \
                patch("socket.socket.connect", side_effect=AssertionError("network")):
            self.assertEqual(main(["--source", "replay", "--provider", "openai"]), 2)
            self.assertEqual(main(["--source", "replay", "--provider", "openai", "--smoke"]), 2)

    def test_load_dotenv_ignores_missing_and_does_not_override(self):
        load_dotenv(Path("definitely-missing-aftershock.env"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "OPENAI_API_KEY=from-file\nINGESTION_AI_MODEL='quoted-model'\nexport INGESTION_DEBUG=1\n",
                encoding="utf-8",
            )
            with patch.dict("os.environ", {"OPENAI_API_KEY": "already-set"}, clear=False):
                load_dotenv(path)
                self.assertEqual(os.environ["OPENAI_API_KEY"], "already-set")
                self.assertEqual(os.environ["INGESTION_AI_MODEL"], "quoted-model")
                self.assertEqual(os.environ["INGESTION_DEBUG"], "1")


class SmokeExtractionTests(unittest.TestCase):
    """Representative A-F extraction contract, mocked; no live OpenAI access."""

    def setUp(self):
        self.source = SocialReplayAdapter(SMOKE_FILE)
        self.normalizer = AIInformationNormalizer(
            FixtureExtractionModel(ROOT / "data/ingestion_ai_responses.json")
        )
        self.by_id = {item.external_id: item for item in self.source.items}

    def extract(self, external_id):
        return self.normalizer.normalize(self.by_id[external_id])

    def test_smoke_set_has_six_representative_inputs(self):
        self.assertEqual(
            [item.external_id for item in self.source.items],
            ["smoke-a", "smoke-b", "smoke-c", "smoke-d", "smoke-e", "smoke-f"],
        )

    def test_trapped_claim_preserves_uncertainty_and_raw_location(self):
        observations = self.extract("smoke-a")
        self.assertEqual(len(observations), 1)
        observation = observations[0]
        validate_structured_observation(observation)
        self.assertEqual(observation["incident_type"], "trapped_victims")
        self.assertEqual(observation["victim_indication"], {
            "type": "possible_multiple", "count_min": None, "count_max": None})
        self.assertEqual(observation["location"]["raw_text"], "Riverside Apartments")
        self.assertIsNone(observation["location"]["latitude"])
        self.assertIsNone(observation["location"]["longitude"])
        self.assertEqual(observation["verification_status"], "unverified")
        self.assertEqual(observation["source_type"], "social")

    def test_structural_damage_does_not_invent_building_or_counts(self):
        observation = self.extract("smoke-b")[0]
        self.assertEqual(observation["incident_type"], "structural_damage")
        self.assertEqual(observation["location"]["raw_text"], "Caraballeda")
        self.assertEqual(observation["victim_indication"]["type"], "unknown")
        self.assertIsNone(observation["victim_indication"]["count_min"])
        self.assertIsNone(observation["victim_indication"]["count_max"])
        self.assertNotIn("site_id", observation)
        self.assertNotIn("worksite_id", observation)
        self.assertIsNone(observation["location"].get("resolved_site_id"))

    def test_blocked_access_keeps_raw_location(self):
        observation = self.extract("smoke-c")[0]
        self.assertEqual(observation["incident_type"], "access_blocked")
        self.assertEqual(observation["access_status"], "blocked")
        self.assertEqual(observation["location"]["raw_text"], "Avenida La Playa")
        self.assertEqual(observation["victim_indication"]["type"], "unknown")
        self.assertIsNone(observation["location"]["latitude"])

    def test_irrelevant_input_yields_zero_observations(self):
        self.assertEqual(self.extract("smoke-d"), [])

    def test_spanish_input_keeps_source_language_excerpt(self):
        raw = self.by_id["smoke-e"]
        self.assertEqual(raw.language, "es")
        observation = self.extract("smoke-e")[0]
        self.assertEqual(observation["incident_type"], "trapped_victims")
        self.assertEqual(observation["victim_indication"]["type"], "possible_multiple")
        self.assertEqual(observation["location"]["raw_text"], "mercado de Caraballeda")
        self.assertIn("mercado de Caraballeda", raw.content)
        self.assertEqual(observation["metadata"]["ingestion"]["language"], "es")
        self.assertIsNone(observation["location"]["longitude"])

    def test_multi_fact_report_yields_multiple_observations(self):
        observations = self.extract("smoke-f")
        self.assertEqual(len(observations), 2)
        self.assertEqual({item["incident_type"] for item in observations},
                         {"structural_damage", "access_blocked"})
        for observation in observations:
            validate_structured_observation(observation)
            self.assertEqual(observation["victim_indication"]["type"], "unknown")
            self.assertIsNone(observation["location"]["latitude"])
            self.assertIsNone(observation["location"]["longitude"])

    def test_provider_schema_omits_identity_and_forbids_coordinates(self):
        schema = extraction_schema()
        self.assertEqual(schema["required"], ["observations"])
        self.assertFalse(schema["additionalProperties"])
        item = schema["properties"]["observations"]["items"]
        self.assertFalse(item["additionalProperties"])
        for name in ("observation_id", "schema_version", "timestamp", "source_type",
                     "source_id", "verification_status", "metadata", "evidence_strength",
                     "raw_source_reference"):
            self.assertNotIn(name, item["properties"])
        self.assertEqual(item["properties"]["location"]["properties"]["latitude"], {"type": "null"})
        self.assertEqual(item["properties"]["location"]["properties"]["longitude"], {"type": "null"})

    def test_offline_smoke_cli(self):
        output = StringIO()
        with patch.dict("os.environ", {}, clear=True), patch("ingestion.demo.load_dotenv"), \
                patch("sys.stdout", output), patch("logging.basicConfig"), \
                patch("socket.socket.connect", side_effect=AssertionError("network")):
            status = main(["--smoke", "--provider", "fixture"])
        self.assertEqual(status, 0)
        observations = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(observations), 6)
        for observation in observations:
            validate_structured_observation(observation)


if __name__ == "__main__":
    unittest.main()

