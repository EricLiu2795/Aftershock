/* Generated from JSON Schema. Run npm run contracts:generate. Do not edit. */

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
