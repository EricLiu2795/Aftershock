/* Generated from JSON Schema. Run npm run contracts:generate. Do not edit. */

/**
 * Durable, resumable event envelope for the command dashboard radio feed.
 */
export type DashboardEvent = {
  schema_version: "2.0";
  event_id: string;
  session_id: string;
  transmission_id: string;
  occurred_at: string;
  event_type:
    | "radio.accepted"
    | "radio.extraction_started"
    | "radio.extraction_retrying"
    | "radio.extracted"
    | "radio.location_resolved"
    | "radio.location_unresolved"
    | "radio.failed";
  payload: {};
} & (
  | {
      event_type?: "radio.accepted";
      payload?: AcceptedPayload;
    }
  | {
      event_type?: "radio.extraction_started";
      payload?: ExtractionStartedPayload;
    }
  | {
      event_type?: "radio.extraction_retrying";
      payload?: ExtractionRetryingPayload;
    }
  | {
      event_type?: "radio.extracted";
      payload?: ExtractedPayload;
    }
  | {
      event_type?: "radio.location_resolved";
      payload?: LocationResolvedPayload;
    }
  | {
      event_type?: "radio.location_unresolved";
      payload?: LocationUnresolvedPayload;
    }
  | {
      event_type?: "radio.failed";
      payload?: FailedPayload;
    }
);
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

export interface AcceptedPayload {
  input_id: string;
  processing_state: "accepted";
}
export interface ExtractionStartedPayload {
  extraction_id: string;
  processing_state: "extracting";
}
export interface ExtractionRetryingPayload {
  extraction_id: string;
  attempt: number;
  max_attempts: number;
  processing_state: "retrying";
  failure: Failure & {
    retryable?: true;
  };
}
export interface Failure {
  code: string;
  message: string;
  retryable: boolean;
}
export interface ExtractedPayload {
  extraction_result: RadioExtractionResult;
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
export interface LocationResolvedPayload {
  observation_id: string;
  resolution: LocationResolution & {
    resolution_status?: "resolved";
  };
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
export interface LocationUnresolvedPayload {
  observation_id: string;
  resolution: LocationResolution & {
    resolution_status?: "ambiguous" | "unresolved";
  };
}
export interface FailedPayload {
  stage: "transcription" | "finalization" | "extraction" | "resolution";
  failure: Failure & {
    retryable?: false;
  };
}
