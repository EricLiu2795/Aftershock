import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import type { RawRadioInput } from "./types.js";
import { SCHEMA_VERSION } from "./types.js";

export const CARABALLEDA_DEMO = "caraballeda-demo";
export const PACK_EVENTS_PATH = fileURLToPath(
  new URL("../../../data/scenarios/caraballeda/scenario_events.json", import.meta.url),
);

const HIDDEN_EVENT_FIELDS = [
  "intended_referent",
  "hidden_truth_linkage",
  "expected_downstream_interpretation",
  "expected_state_transition",
  "verification_summary",
  "revealed_asset_ids",
] as const;

const FORBIDDEN_MARKERS = [
  "intended_referent",
  "hidden_truth_linkage",
  "expected_downstream_interpretation",
  "expected_state_transition",
  "scenario_ground_truth",
  "verification_summary",
] as const;

export const DEMO_STEPS = [
  { event_id: "E01", delay_ms: 0, source_type: "social" },
  { event_id: "E02", delay_ms: 2000, source_type: "radio" },
  { event_id: "E11", delay_ms: 6000, source_type: "social" },
] as const;

export const DEMO_OMITTED = [
  { event_id: "E12", source_type: "drone", reason: "drone_verification" },
] as const;

export const SITE_ID_SHORTCUTS = [
  "osm:way:1533952351",
  "osm:way:1533952352",
  "osm:node:13976280529",
] as const;

interface PackEvent {
  event_id: string;
  source_type: "social" | "radio" | "drone";
  source_id: string;
  raw_input: Record<string, unknown>;
  [key: string]: unknown;
}

let cached: { byId: Map<string, PackEvent>; events: PackEvent[] } | undefined;

export function loadPackEvents(): { byId: Map<string, PackEvent>; events: PackEvent[] } {
  if (cached) return cached;
  const payload = JSON.parse(readFileSync(PACK_EVENTS_PATH, "utf8")) as {
    events: PackEvent[];
  };
  cached = {
    events: payload.events,
    byId: new Map(payload.events.map((event) => [event.event_id, event])),
  };
  return cached;
}

export function packEvent(eventId: string): PackEvent {
  const event = loadPackEvents().byId.get(eventId);
  if (!event) throw new Error(`Unknown scenario pack event: ${eventId}`);
  return event;
}

export function observableRawInput(event: PackEvent): Record<string, unknown> {
  const raw = structuredClone(event.raw_input);
  assertObservablePayload(raw);
  return raw;
}

export function runtimeSourceId(event: PackEvent): string {
  const raw = event.raw_input;
  if (event.source_type === "social") {
    const author = stringField(raw.author_id);
    if (author) return author;
    const platform = stringField(raw.platform);
    if (platform && platform !== "replay") return platform;
    if (event.source_id && event.source_id !== "replay") return event.source_id;
    return "social";
  }
  if (event.source_type === "radio") {
    return stringField(raw.speaker_id) || event.source_id || "radio";
  }
  return event.source_id || event.source_type;
}

export function packRadioRaw(
  sessionId: string,
  transmissionId: string,
  inputId: string,
  eventId = "E02",
): RawRadioInput {
  const event = packEvent(eventId);
  if (event.source_type !== "radio") {
    throw new Error(`${eventId} is not a radio event`);
  }
  const raw = observableRawInput(event);
  const metadata =
    raw.metadata && typeof raw.metadata === "object" && !Array.isArray(raw.metadata)
      ? (raw.metadata as Record<string, unknown>)
      : {};
  const payload: RawRadioInput = {
    schema_version: SCHEMA_VERSION,
    input_id: inputId,
    timestamp: String(raw.timestamp),
    audio_reference: stringField(raw.audio_reference) ?? null,
    transcript: String(raw.transcript),
    channel: stringField(raw.channel) ?? null,
    speaker_id: stringField(raw.speaker_id) ?? null,
    metadata: {
      ...metadata,
      capture_mode: "cached_observation",
      transmission_id: transmissionId,
      session_id: sessionId,
    },
  };
  assertObservablePayload(payload);
  return payload;
}

export function stablePackRadioInputId(sessionId: string, eventId: string): string {
  const raw = packEvent(eventId).raw_input;
  return `pack:${sessionId}:${String(raw.input_id)}`;
}

export function assertObservablePayload(value: unknown): void {
  const leaked = forbiddenKey(value);
  if (leaked) {
    throw new Error(`hidden scenario field leaked into runtime payload: ${leaked}`);
  }
  const blob = JSON.stringify(value);
  for (const marker of FORBIDDEN_MARKERS) {
    if (blob.includes(marker)) {
      throw new Error(`hidden scenario field leaked into runtime payload: ${marker}`);
    }
  }
  for (const siteId of SITE_ID_SHORTCUTS) {
    if (blob.includes(siteId)) {
      throw new Error(`canonical site id shortcut leaked into runtime payload: ${siteId}`);
    }
  }
  for (const field of HIDDEN_EVENT_FIELDS) {
    if (blob.includes(`"${field}"`)) {
      throw new Error(`${field} leaked into runtime payload`);
    }
  }
}

function forbiddenKey(value: unknown): string | undefined {
  if (Array.isArray(value)) {
    for (const item of value) {
      const found = forbiddenKey(item);
      if (found) return found;
    }
    return undefined;
  }
  if (!value || typeof value !== "object") return undefined;
  for (const [key, item] of Object.entries(value)) {
    if (
      (FORBIDDEN_MARKERS as readonly string[]).includes(key) ||
      (HIDDEN_EVENT_FIELDS as readonly string[]).includes(key)
    ) {
      return key;
    }
    const found = forbiddenKey(item);
    if (found) return found;
  }
  return undefined;
}

function stringField(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}
