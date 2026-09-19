/* Generated from JSON Schema. Run npm run contracts:generate. Do not edit. */

/**
 * Deterministic mapping from an observation to a canonical Site, retaining unresolved and ambiguous results.
 */
export interface LocationResolution {
  schema_version: "2.1";
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
