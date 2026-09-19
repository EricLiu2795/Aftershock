/** Dashboard read model. Canonical observations retain the shared JSON Schema contract. */
import type { StructuredObservation } from "./generated/observation";
export interface MapSite {
  id: string;
  name: string;
  longitude: number;
  latitude: number;
  facility: string | null;
  height: number;
  heightSource: "osm" | "levels_estimate" | "display_default";
}
export interface IntelligenceReport {
  id: string;
  source: string;
  author: string;
  timestamp: string;
  text: string;
  mode: string;
  state: string;
  observations: StructuredObservation[];
  locations: Array<{
    site: MapSite;
    method: "exact_name" | "manual";
    locatedAt?: string;
  }>;
  sourceUrl: string | null;
  model: string | null;
}
export interface IntelligenceSnapshot {
  reports: IntelligenceReport[];
  updatedAt: string;
  scenario: string;
}
export interface GeographyInfo {
  sites: MapSite[];
  roadCount: number;
  attribution: string;
  bounds: [number, number, number, number];
}
export interface ExternalReport {
  id: string;
  source: string;
  author: string;
  timestamp: string;
  text: string;
  mode: string;
  observations: StructuredObservation[];
  sourceUrl: string | null;
  model: string;
}
