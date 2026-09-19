"""Run from the repository root: python -m ingestion.demo"""

import argparse
import json
import logging

from jsonschema import ValidationError

from ingestion.adapters import GDACSAdapter, ReliefWebAdapter, SocialReplayAdapter
from ingestion.config import ROOT, Config, load_dotenv
from ingestion.http import JsonHttpClient
from ingestion.normalizer import AIInformationNormalizer, IngestionPipeline, NormalizationError
from ingestion.providers import FixtureExtractionModel, OpenAIExtractionModel

SMOKE_FILE = ROOT / "data/openai_smoke_inputs.json"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Emit schema-valid observations as JSONL; logs go to stderr.")
    parser.add_argument("--source", choices=["replay", "gdacs", "reliefweb"])
    parser.add_argument("--provider", choices=["fixture", "openai"])
    parser.add_argument("--smoke", action="store_true",
                        help="Process the small representative extraction set. Use --provider openai for a live sanity check.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    log = logging.getLogger(__name__)
    try:
        load_dotenv()
        config = Config.from_env()
        source_name = args.source or ("replay" if config.mode == "replay" else "gdacs")
        provider_name = args.provider or config.provider
        if args.smoke:
            if args.source in {"gdacs", "reliefweb"}:
                parser.error("--smoke uses the local representative set; do not combine with live sources")
            source_name = "replay"
        if provider_name == "fixture" and source_name != "replay":
            parser.error("fixture AI only supports local replay; live sources require --provider openai")
        if provider_name == "openai" and (not str(config.api_key).strip() or not str(config.ai_model).strip()):
            log.error("live OpenAI extraction requires OPENAI_API_KEY and INGESTION_AI_MODEL")
            return 2
        http = JsonHttpClient(config.timeout)
        if args.smoke:
            source = SocialReplayAdapter(SMOKE_FILE)
        elif source_name == "replay":
            source = SocialReplayAdapter(config.replay_file)
        elif source_name == "gdacs":
            source = GDACSAdapter(http=http, base_url=config.gdacs_url, event_types=config.gdacs_types,
                                  event_id=config.gdacs_event_id, country=config.country,
                                  since=config.since, until=config.until)
        else:
            source = ReliefWebAdapter(http=http, base_url=config.reliefweb_url,
                                     appname=config.reliefweb_appname, query=config.reliefweb_query,
                                     country=config.country, since=config.since, until=config.until)
        model = (FixtureExtractionModel(config.fixture_file) if provider_name == "fixture" else
                 OpenAIExtractionModel(api_key=config.api_key, model=config.ai_model,
                                       base_url=config.ai_base_url, http=JsonHttpClient(config.ai_timeout)))
        pipeline = IngestionPipeline(AIInformationNormalizer(model))
        log.info("ingestion_started source=%s provider=%s", source_name, provider_name)
        failures = total = 0
        while True:
            items = source.fetch()
            if getattr(source, "last_error", None):
                failures += 1
            if not items:
                break
            for raw in items:
                try:
                    for observation in pipeline.ingest(raw):
                        print(json.dumps(observation, ensure_ascii=True, allow_nan=False))
                        total += 1
                except NormalizationError as error:
                    log.warning("input_failed event=%s code=%s", raw.identity[:12], str(error))
                    failures += 1
            if source_name != "replay":
                break  # Single bounded live page per invocation.
        log.info("ingestion_finished observations=%d failures=%d", total, failures)
        return 1 if failures else 0
    except (ValueError, OSError, ValidationError, TypeError, KeyError):
        log.error("configuration_or_fixture_error; check environment and fixture paths")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
