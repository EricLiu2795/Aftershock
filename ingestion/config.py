"""Environment-only configuration. No network use.

`Config.from_env()` reads the process environment only. The demo CLI may call
`load_dotenv()` first so a local gitignored `.env` can supply placeholders the
developer pasted; existing process variables always win. Values are never logged.
"""

from dataclasses import dataclass, field
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_dotenv(path: Path | None = None) -> None:
    """Load KEY=VALUE pairs from a local .env without overriding the process environment.

    Values are never logged. Missing files are ignored. Existing environment
    variables win so CI and shells remain the source of truth when set.
    """
    env_path = path or ROOT / ".env"
    try:
        text = env_path.read_text(encoding="utf-8").lstrip("\ufeff")
    except OSError:
        return
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value


@dataclass
class Config:
    mode: str = "replay"
    provider: str = "fixture"
    ai_model: str = ""
    api_key: str = field(default="", repr=False)
    ai_base_url: str = "https://api.openai.com/v1"
    timeout: float = 15
    ai_timeout: float = 45
    country: str | None = None
    since: str | None = None
    until: str | None = None
    gdacs_url: str = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
    gdacs_types: str = "EQ"
    gdacs_event_id: str | None = None
    reliefweb_url: str = "https://api.reliefweb.int/v2/reports"
    reliefweb_appname: str = ""
    reliefweb_query: str = "earthquake"
    replay_file: Path = ROOT / "data/social_replay_caraballeda.json"
    fixture_file: Path = ROOT / "data/ingestion_ai_responses.json"

    @classmethod
    def from_env(cls):
        env = os.environ
        config = cls(
            mode=env.get("INGESTION_MODE", "replay"),
            provider=env.get("INGESTION_AI_PROVIDER", "fixture"),
            ai_model=env.get("INGESTION_AI_MODEL", ""), api_key=env.get("OPENAI_API_KEY", ""),
            ai_base_url=env.get("INGESTION_AI_BASE_URL", cls.ai_base_url),
            timeout=float(env.get("INGESTION_HTTP_TIMEOUT", "15")),
            ai_timeout=float(env.get("INGESTION_AI_TIMEOUT", "45")),
            country=env.get("INGESTION_COUNTRY") or None,
            since=env.get("INGESTION_SINCE") or None, until=env.get("INGESTION_UNTIL") or None,
            gdacs_url=env.get("GDACS_API_URL", cls.gdacs_url),
            gdacs_types=env.get("GDACS_EVENT_TYPES", "EQ"),
            gdacs_event_id=env.get("GDACS_EVENT_ID") or None,
            reliefweb_url=env.get("RELIEFWEB_API_URL", cls.reliefweb_url),
            reliefweb_appname=env.get("RELIEFWEB_APPNAME", ""),
            reliefweb_query=env.get("RELIEFWEB_QUERY", "earthquake"),
            replay_file=Path(env.get("INGESTION_REPLAY_FILE", str(cls.replay_file))),
            fixture_file=Path(env.get("INGESTION_FIXTURE_FILE", str(cls.fixture_file))),
        )
        if config.mode not in {"live", "replay"} or config.provider not in {"openai", "fixture"}:
            raise ValueError("unsupported INGESTION_MODE or INGESTION_AI_PROVIDER")
        return config
