/* Generated from JSON Schema. Run npm run contracts:generate. Do not edit. */

/**
 * Normalized facts extracted from raw input; it contains no operational decision or AI-invented worksite identity.
 */
export interface StructuredObservation {
  schema_version: "2.0";
  observation_id: string;
  timestamp: string;
  source_type: "radio" | "social" | "drone" | "field_note" | "gdacs" | "reliefweb" | "ushahidi" | "system";
  source_id?: string | null;
  location: Location;
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
  verification_status: "potential" | "unverified" | "corroborated" | "verified";
  evidence_strength?: EvidenceStrength;
  raw_source_reference?: RawSourceReference;
  metadata?: {
    [k: string]: unknown;
  };
}
export interface Location {
  latitude: number | null;
  longitude: number | null;
  raw_text?: string | null;
  label?: string | null;
  accuracy?: "exact" | "building" | "street" | "area" | "unknown" | null;
  resolved_site_id?: string | null;
}
export interface VictimIndication {
  type:
    "none" | "possible_individual" | "possible_multiple" | "confirmed_individual" | "confirmed_multiple" | "unknown";
  count_min?: number | null;
  count_max?: number | null;
}
export interface EvidenceStrength {
  level: "weak" | "moderate" | "strong";
  score: number;
  notes?: string;
}
export interface RawSourceReference {
  reference_type: string;
  value: string;
  uri?: string | null;
}
