import "dotenv/config";
import type { AppConfig } from "./types.js";

function integer(name: string, fallback: number): number {
  const value = process.env[name];
  if (value === undefined) return fallback;
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new Error(`${name} must be a positive integer`);
  }
  return parsed;
}

function nonNegativeInteger(name: string, fallback: number): number {
  const value = process.env[name];
  if (value === undefined) return fallback;
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed) || parsed < 0) {
    throw new Error(`${name} must be a non-negative integer`);
  }
  return parsed;
}

function bool(name: string, fallback: boolean): boolean {
  const value = process.env[name];
  return value === undefined ? fallback : value.toLowerCase() === "true";
}

/** Local demo: treat localhost and 127.0.0.1 as the same browser origin. */
export function isAllowedOrigin(
  origin: string | undefined,
  appOrigin: string,
): boolean {
  if (!origin) return true;
  if (origin === appOrigin) return true;
  try {
    const configured = new URL(appOrigin);
    const incoming = new URL(origin);
    const loopback = new Set(["localhost", "127.0.0.1"]);
    return (
      incoming.protocol === configured.protocol &&
      incoming.port === configured.port &&
      loopback.has(incoming.hostname) &&
      loopback.has(configured.hostname)
    );
  } catch {
    return false;
  }
}

export function loadConfig(overrides: Partial<AppConfig> = {}): AppConfig {
  const config: AppConfig = {
    host: process.env.HOST ?? "0.0.0.0",
    port: integer("PORT", 3001),
    appOrigin: process.env.APP_ORIGIN ?? "http://localhost:5173",
    databasePath: process.env.DATABASE_PATH ?? "./data/aftershock.sqlite",
    sessionSigningSecret:
      process.env.SESSION_SIGNING_SECRET ?? "development-only-signing-secret-change-me",
    presenterApiKey: process.env.PRESENTER_API_KEY ?? "development-presenter-key",
    cookieSecure: bool("COOKIE_SECURE", process.env.NODE_ENV === "production"),
    sessionTtlHours: integer("SESSION_TTL_HOURS", 24),
    joinCodeTtlMinutes: integer("JOIN_CODE_TTL_MINUTES", 15),
    maxTranscriptCharacters: integer("MAX_TRANSCRIPT_CHARACTERS", 10_000),
    maxRequestBytes: integer("MAX_REQUEST_BYTES", 65_536),
    tokenRequestsPerMinute: integer("TOKEN_REQUESTS_PER_MINUTE", 6),
    sessionTokenRequestsPerMinute: integer("SESSION_TOKEN_REQUESTS_PER_MINUTE", 30),
    elevenLabsApiKey: process.env.ELEVENLABS_API_KEY,
    elevenLabsApiBaseUrl: process.env.ELEVENLABS_API_BASE_URL ?? "https://api.elevenlabs.io",
    llmApiKey: process.env.LLM_API_KEY,
    llmApiBaseUrl: process.env.LLM_API_BASE_URL ?? "https://api.openai.com/v1",
    llmModel: process.env.LLM_MODEL,
    llmTimeoutMs: integer("LLM_TIMEOUT_MS", 20_000),
    extractionPollMs: integer("EXTRACTION_POLL_MS", 250),
    extractionLeaseMs: integer("EXTRACTION_LEASE_MS", 30_000),
    extractionMaxAttempts: integer("EXTRACTION_MAX_ATTEMPTS", 2),
    replayEnabled: bool("REPLAY_ENABLED", true),
    replayReleaseIntervalS: nonNegativeInteger("REPLAY_RELEASE_INTERVAL_S", 120),
    startWorker: true,
    ...overrides,
  };

  if (config.sessionSigningSecret.length < 32) {
    throw new Error("SESSION_SIGNING_SECRET must contain at least 32 characters");
  }
  if (
    process.env.NODE_ENV === "production" &&
    (config.sessionSigningSecret === "development-only-signing-secret-change-me" ||
      config.presenterApiKey === "development-presenter-key")
  ) {
    throw new Error("Production requires explicit session and presenter secrets");
  }
  return config;
}
