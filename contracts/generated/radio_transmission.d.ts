/* Generated from JSON Schema. Run npm run contracts:generate. Do not edit. */

/**
 * Server-owned transport record for one reserved or finalized radio transmission.
 */
export type RadioTransmission = {
  schema_version: "2.0";
  transmission_id: string;
  session_id: string;
  responder_id: string;
  channel: string;
  capture_mode: "live_microphone" | "audio_replay" | "cached_transcript" | "cached_observation";
  processing_state:
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
  created_at: string;
  updated_at: string;
  accepted_at?: string;
  input_id?: string;
  transcript?: string;
  provider_session_reference?: string;
  capture_started_at?: string;
  capture_ended_at?: string;
  /**
   * @minItems 1
   * @maxItems 1000
   */
  segments?: Segment[];
  extraction_result?: RadioExtractionResult;
  location_resolutions?: LocationResolution[];
  failure?: Failure;
};
/**
 * Validated semantic facts extracted from one radio source. Observation identities and trusted provenance are added by the backend.
 */
export type RadioExtractionResult = {
  schema_version: "2.0";
  extraction_id: string;
  input_id: string;
  transmission_id: string;
  result_status: "extracted" | "no_actionable_content" | "failed";
  /**
   * @maxItems 20
   */
  observations: SemanticObservation[];
  completed_at: string;
  prompt_version: string;
  model_reference: string;
  failure?: Failure;
};

export interface Segment {
  sequence: number;
  text: string;
  started_at_ms: number;
  ended_at_ms: number;
  provider_segment_id?: string;
}
/**
 * Semantic content only; internal IDs, coordinates, verification upgrades, priorities, and assignments are intentionally not fields.
 */
export interface SemanticObservation {
  location_text: string | null;
  incident_type:
    | "structural_damage"
    | "trapped_victims"
    | "medical_need"
    | "fire_or_secondary_hazard"
    | "access_blocked"
    | "evacuation_need";
  victim_indication: VictimIndication;
  structural_hazard:
    | "none"
    | "reported_damage"
    | "minor_damage"
    | "major_damage"
    | "severe_collapse"
    | "active_secondary_hazard"
    | "unknown";
  access_status: "open" | "possibly_blocked" | "blocked" | "limited" | "unknown";
  /**
   * @minItems 1
   */
  supporting_text: string[];
}
export interface VictimIndication {
  type:
    "none" | "possible_individual" | "possible_multiple" | "confirmed_individual" | "confirmed_multiple" | "unknown";
  count_min?: number | null;
  count_max?: number | null;
}
export interface Failure {
  code: string;
  message: string;
  retryable: boolean;
}
/**
 * Deterministic mapping from an observation to a canonical Site, retaining unresolved and ambiguous results.
 */
export interface LocationResolution {
  schema_version: "2.0";
  observation_id: string;
  resolution_status: "resolved" | "ambiguous" | "unresolved";
  resolved_site_id?: string | null;
  resolution_method?: "gps_point_in_polygon" | "address_geocode" | "gazetteer" | "nearest_site" | "manual" | null;
  resolution_score?: number | null;
  candidate_site_ids?: string[];
  metadata?: {
    [k: string]: unknown;
  };
}
