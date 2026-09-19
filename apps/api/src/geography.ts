import { readFileSync } from "node:fs";
import type {
  MapSite,
  GeographyInfo,
} from "../../../contracts/intelligence.js";

interface Feature {
  type: "Feature";
  id: string;
  geometry: { type: string; coordinates: unknown };
  properties: {
    site?: {
      site_id: string;
      display_name: string;
      centroid: { longitude: number; latitude: number };
      facility_type?: string;
      metadata?: { osm_tags?: Record<string, string> };
    };
    osm_tags?: Record<string, string>;
  };
}
let cached: ReturnType<typeof load> | undefined;
const normalize = (value: string) =>
  value
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
function load() {
  const raw = JSON.parse(
    readFileSync(
      new URL(
        "../../../data/graph_exports/caraballeda.geojson",
        import.meta.url,
      ),
      "utf8",
    ),
  ) as { features: Feature[]; attribution: string };
  const sites: MapSite[] = [];
  const features = raw.features.map((feature) => {
    const site = feature.properties.site;
    if (!site)
      return {
        ...feature,
        properties: {
          kind: "road",
          id: feature.id,
          name: feature.properties.osm_tags?.name ?? "",
          highway: feature.properties.osm_tags?.highway ?? "",
        },
      };
    const tags = site.metadata?.osm_tags ?? {};
    const height = Number.parseFloat(tags.height ?? "");
    const levels = Number.parseFloat(tags["building:levels"] ?? "");
    const hasHeight = Number.isFinite(height) && height > 0;
    const hasLevels = Number.isFinite(levels) && levels > 0;
    const item: MapSite = {
      id: site.site_id,
      name: site.display_name,
      ...site.centroid,
      facility: site.facility_type ?? null,
      height: Math.min(300, hasHeight ? height : hasLevels ? levels * 3 : 6),
      heightSource: hasHeight
        ? "osm"
        : hasLevels
          ? "levels_estimate"
          : "display_default",
    };
    sites.push(item);
    return { ...feature, properties: { ...item, kind: "site" } };
  });
  const info: GeographyInfo = {
    sites,
    roadCount: raw.features.length - sites.length,
    attribution: raw.attribution,
    bounds: [-66.87, 10.59, -66.81, 10.64],
  };
  const byId = new Map(sites.map((site) => [site.id, site]));
  const byName = new Map<string, MapSite[]>();
  for (const site of sites) {
    if (site.name.startsWith("OSM ")) continue;
    const name = normalize(site.name);
    byName.set(name, [...(byName.get(name) ?? []), site]);
  }
  return {
    info,
    geojson: { type: "FeatureCollection", features },
    byId,
    byName,
  };
}
export function geography() {
  return (cached ??= load());
}
/** Exact, unique gazetteer match only. A building across from a pharmacy is not a location. */
export function matchSite(
  text: string | null | undefined,
): MapSite | undefined {
  const matches = geography().byName.get(normalize(text ?? ""));
  return matches?.length === 1 ? matches[0] : undefined;
}
