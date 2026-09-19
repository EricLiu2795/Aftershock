export const SCHEMA_VERSION = "2.1" as const;

export type CaptureMode =
  | "live_microphone"
  | "audio_replay"
  | "cached_transcript"
  | "cached_observation";

export type TransmissionState =
  | "reserved"
  | "accepted"
  | "extracting"
  | "retrying"
  | "extracted"
  | "resolving"
  | "resolved"
  | "unresolved"
  | "failed"
  | "cancelled";

export interface RawRadioInput {
  schema_version: "2.1";
  input_id: string;
  timestamp: string;
  audio_reference: string | null;
  transcript: string;
  channel: string | null;
  speaker_id: string | null;
  metadata: Record<string, unknown>;
}

import type {
  SemanticObservation as ContractSemanticObservation,
  VictimIndication as ContractVictimIndication,
} from "../../../contracts/generated/radio_extraction_result.js";
export type VictimIndication = Required<ContractVictimIndication>;
export type SemanticObservation = Omit<
  ContractSemanticObservation,
  "victim_indication"
> & { victim_indication: VictimIndication };

export interface ExtractionResult {
  result: "observations" | "no_actionable_content";
  observations: SemanticObservation[];
  adapter: "deterministic" | "llm" | "cached";
  model: string;
  prompt_version: string;
}

export interface StructuredObservation {
  schema_version: "2.1";
  observation_id: string;
  timestamp: string;
  source_type: "radio";
  source_id: string | null;
  location: {
    latitude: null;
    longitude: null;
    raw_text: string | null;
    label: string | null;
    accuracy: "unknown";
    resolved_site_id: string | null;
  };
  incident_type: SemanticObservation["incident_type"];
  victim_indication: VictimIndication;
  structural_hazard: SemanticObservation["structural_hazard"];
  access_status: SemanticObservation["access_status"];
  verification_status: "unverified";
  raw_source_reference: {
    reference_type: "raw_radio_input";
    value: string;
  };
  metadata: Record<string, unknown>;
}

export interface LocationResolution {
  schema_version: "2.1";
  observation_id: string;
  resolution_status: "resolved" | "ambiguous" | "unresolved";
  resolved_site_id: string | null;
  resolution_method: "gazetteer" | null;
  resolution_score: number | null;
  candidate_site_ids: string[];
  metadata: Record<string, unknown>;
}

export type { RadioFinalizeRequest as FinalizeBody } from "../../../contracts/generated/radio_finalize_request.js";

export interface AppConfig {
  host: string;
  port: number;
  appOrigin: string;
  databasePath: string;
  sessionSigningSecret: string;
  presenterApiKey: string;
  cookieSecure: boolean;
  sessionTtlHours: number;
  joinCodeTtlMinutes: number;
  maxTranscriptCharacters: number;
  maxRequestBytes: number;
  tokenRequestsPerMinute: number;
  sessionTokenRequestsPerMinute: number;
  elevenLabsApiKey?: string;
  elevenLabsApiBaseUrl: string;
  llmApiKey?: string;
  llmApiBaseUrl: string;
  llmModel?: string;
  llmTimeoutMs: number;
  extractionPollMs: number;
  extractionLeaseMs: number;
  extractionMaxAttempts: number;
  replayEnabled: boolean;
  startWorker: boolean;
}
