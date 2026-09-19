"""Bounded dashboard bridge through the existing adapters, normalizer and validator.

stdout is one JSON envelope; failures never silently fall back to fixture output.
"""
import argparse
import json
import logging
import sys

from ingestion.adapters import SocialReplayAdapter, GDACSAdapter, ReliefWebAdapter
from ingestion.config import Config, load_dotenv
from ingestion.http import JsonHttpClient
from ingestion.normalizer import AIInformationNormalizer, IngestionPipeline
from ingestion.providers import FixtureExtractionModel, OpenAIExtractionModel


def collect(source_name='replay', provider_name='fixture', *, known_ids=()):
    config = Config.from_env()
    if provider_name == 'openai' and (not config.api_key or not config.ai_model):
        raise ValueError('OPENAI_API_KEY and INGESTION_AI_MODEL are required')
    if source_name != 'replay' and provider_name != 'openai':
        raise ValueError('Live sources require OpenAI extraction')
    http = JsonHttpClient(config.timeout)
    if source_name == 'replay':
        source = SocialReplayAdapter(config.replay_file)
    elif source_name == 'gdacs':
        source = GDACSAdapter(http=http, base_url=config.gdacs_url, event_types=config.gdacs_types,
                             event_id=config.gdacs_event_id, country=config.country or "Venezuela",
                             since=config.since, until=config.until, limit=10)
    else:
        source = ReliefWebAdapter(http=http, base_url=config.reliefweb_url, appname=config.reliefweb_appname,
                                 query=config.reliefweb_query, country=config.country or "Venezuela",
                                 since=config.since, until=config.until, limit=10)
    model = (FixtureExtractionModel(config.fixture_file) if provider_name == 'fixture' else
             OpenAIExtractionModel(api_key=config.api_key, model=config.ai_model,
                                   base_url=config.ai_base_url, http=JsonHttpClient(config.ai_timeout)))
    pipeline = IngestionPipeline(AIInformationNormalizer(model))
    seen = set(known_ids)
    reports = []
    while True:
        items = source.fetch()
        if getattr(source, 'last_error', None):
            raise RuntimeError('Source could not be fetched')
        if not items:
            break
        for raw in items:
            report_id = 'external:' + raw.identity
            if report_id in seen:
                continue
            observations = pipeline.ingest(raw)
            seen.add(report_id)
            reports.append({
                'id': report_id, 'source': raw.source_type,
                'author': str(raw.structured_metadata.get('author_id') or raw.source_id),
                'timestamp': raw.timestamp, 'text': raw.content,
                'mode': 'social_replay' if source_name == 'replay' else 'live_external',
                'observations': observations, 'sourceUrl': raw.source_url,
                'model': 'fixture' if provider_name == 'fixture' else config.ai_model,
            })
        if source_name != 'replay':
            break
    return reports


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', choices=['replay', 'gdacs', 'reliefweb'], default='replay')
    parser.add_argument('--provider', choices=['fixture', 'openai'], default='fixture')
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    load_dotenv()
    try:
        known_ids = json.load(sys.stdin).get('known_ids', [])
        print(json.dumps({'reports': collect(args.source, args.provider, known_ids=known_ids)}))
        return 0
    except Exception:
        # Provider exceptions may contain response bodies. Keep them out of the API response.
        print('Ingestion failed. Check Python dependencies, source settings, and provider credentials.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
