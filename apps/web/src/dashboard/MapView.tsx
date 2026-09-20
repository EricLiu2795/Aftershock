import { reportGeometry } from "./report-geometry";
import { DroneMapOverlay } from "./DroneMapOverlay";
import { ScanPhotoOverlay } from "./ScanPhotoOverlay";
import type { TrackingFrame } from "./drone-tracking";
import type { BeliefRoadState } from "./demo-state";
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import { useEffect, useRef, useState } from "react";
import type {
  Map as LibreMap,
  GeoJSONSource,
  ExpressionSpecification,
  StyleSpecification,
  MapOptions,
} from "maplibre-gl";
import type { FeatureCollection } from "geojson";
import {
  Compass,
  Crosshair,
  Layers3,
  Minus,
  Plus,
  Mountain,
  MapPin,
  Ambulance,
  List,
} from "lucide-react";
import type {
  GeographyInfo,
  IntelligenceReport,
  MapSite,
} from "../../../../contracts/intelligence";
import type { SaveState } from "../saveplanner/client";
import { unitColour } from "../saveplanner/client";
import "maplibre-gl/dist/maplibre-gl.css";
import "mapbox-gl/dist/mapbox-gl.css";
import "../saveplanner/saveplanner.css";

const empty: FeatureCollection = { type: "FeatureCollection", features: [] };
const token = import.meta.env.VITE_MAPBOX_ACCESS_TOKEN?.trim();
const localStyle: StyleSpecification = {
  version: 8,
  sources: {},
  layers: [
    {
      id: "background",
      type: "background",
      paint: { "background-color": "#081623" },
    },
  ],
};
const home = {
  center: [-66.843, 10.612] as [number, number],
  zoom: 14.9,
  pitch: 55,
  bearing: -18,
};
interface Props {
  droneTracking?: TrackingFrame;
  save?: SaveState;
  operationalRoads?: BeliefRoadState[];
  geography?: GeographyInfo;
  reports: IntelligenceReport[];
  selected?: IntelligenceReport;
  selectedSite?: MapSite;
  onReport(id: string): void;
  onSite(site: MapSite): void;
  onRoadClick?(edgeId: string, blocked: boolean): void;
}
export function MapView(props: Props) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<LibreMap | undefined>(undefined);
  const loadedGeography = useRef<FeatureCollection>(empty);
  const markerCtor = useRef<any>(undefined);
  const unitMarkers = useRef(new Map<string, any>());
  const latest = useRef(props);
  latest.current = props;
  const [ready, setReady] = useState(false);
  const [notice, setNotice] = useState("Loading Caraballeda geography…");
  const [localOnly, setLocalOnly] = useState(false);
  const [is3d, set3d] = useState(true);
  const [buildings] = useState(true);
  const [roads, setRoads] = useState(false);
  const [terrain, setTerrain] = useState(true);
  const [layersOpen, setLayersOpen] = useState(false);
  const [legendOpen, setLegendOpen] = useState(false);
  const [failed, setFailed] = useState(false);
  const [center, setCenter] = useState(home.center);
  useEffect(() => {
    let disposed = false;
    let resize: ResizeObserver | undefined;
    let timeout: ReturnType<typeof setTimeout> | undefined;
    async function init() {
      try {
        // Only load the selected renderer. Both expose the GL JS APIs used here.
        const useMapbox = token && !localOnly;
        const engine = useMapbox
          ? await import("mapbox-gl")
          : await import("maplibre-gl");
        // MapLibre v6 ships its worker separately. Vite must emit that asset
        // explicitly; its default sibling URL does not survive dependency bundling.
        if ("setWorkerUrl" in engine) engine.setWorkerUrl(maplibreWorkerUrl);
        const response = await fetch("/api/geography/features");
        if (!response.ok) throw new Error("Could not load map geometry");
        loadedGeography.current = await response.json() as FeatureCollection;
        markerCtor.current = engine.Marker as typeof markerCtor.current;
        if (disposed || !container.current) return;
        const Constructor = engine.Map as unknown as new (
          options: MapOptions & { accessToken?: string },
        ) => LibreMap;
        const map = new Constructor({
          container: container.current,
          ...home,
          ...(useMapbox ? { accessToken: token } : {}),
          style: localOnly
            ? localStyle
            : token
              ? "mapbox://styles/mapbox/standard-satellite"
              : "https://tiles.openfreemap.org/styles/dark",
          attributionControl: { compact: true },
          maxPitch: 75,
          minZoom: 11,
          maxZoom: 19,
        });
        mapRef.current = map;
        map.on("moveend", () => {
          const center = map.getCenter();
          setCenter([center.lng, center.lat]);
        });
        map.on("error", () => {
          if (!disposed)
            setNotice(
              token
                ? "Map service unavailable. Check the Mapbox token or use the local map."
                : "Some map tiles are unavailable. The local geography can still be used.",
            );
        });
        timeout = setTimeout(() => {
          if (!disposed)
            setNotice(
              "Map is taking longer to load. You can switch to the local map.",
            );
        }, 18000);
        map.on("load", () => {
          if (disposed) return;
          clearTimeout(timeout);
          map.addSource("geography", {
            type: "geojson",
            data: loadedGeography.current,
            attribution:
              '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap contributors</a> · Geofabrik · ODbL',
          });
          map.addSource("reports", { type: "geojson", data: empty });
          map.addSource("selection", { type: "geojson", data: empty });
          map.addSource("save-routes", { type: "geojson", data: empty });
          if (!localOnly) {
            map.addSource(
              "elevation",
              token
                ? {
                    type: "raster-dem",
                    url: "mapbox://mapbox.mapbox-terrain-dem-v1",
                    tileSize: 512,
                    maxzoom: 14,
                  }
                : {
                    type: "raster-dem",
                    url: "https://tiles.mapterhorn.com/tilejson.json",
                    tileSize: 512,
                    attribution:
                      'Terrain: <a href="https://mapterhorn.com">Mapterhorn</a>',
                  },
            );
            map.setTerrain({ source: "elevation", exaggeration: 1 });
          }
          map.addLayer({
            id: "local-roads",
            type: "line",
            source: "geography",
            filter: ["==", ["get", "kind"], "road"],
            layout: { visibility: localOnly ? "visible" : "none" },
            paint: {
              "line-color": "#4a788b",
              "line-width": [
                "interpolate",
                ["linear"],
                ["zoom"],
                12,
                0.6,
                17,
                3,
              ],
              "line-opacity": 0.65,
            },
          });
          map.addLayer({
            id: "local-buildings",
            type: "fill-extrusion",
            source: "geography",
            filter: [
              "in",
              ["geometry-type"],
              ["literal", ["Polygon", "MultiPolygon"]],
            ],
            paint: {
              "fill-extrusion-height": ["get", "height"],
              "fill-extrusion-color": "#6893a6",
              "fill-extrusion-opacity": 0.68,
            },
          });
          map.addLayer({
            id: "radio-alert-building",
            type: "fill-extrusion",
            source: "geography",
            filter: ["in", ["get", "id"], ["literal", []]],
            paint: {
              "fill-extrusion-height": ["+", ["get", "height"], 0.5],
              "fill-extrusion-color": "#ffd84d",
              "fill-extrusion-opacity": 1,
            },
          });
          map.addLayer({
            id: "selected-building",
            type: "fill-extrusion",
            source: "geography",
            filter: ["==", ["get", "id"], ""],
            paint: {
              "fill-extrusion-height": ["+", ["get", "height"], 1],
              "fill-extrusion-color": "#53def6",
              "fill-extrusion-opacity": 0.85,
            },
          });
          map.addLayer({ id: "reported-building-outline", type: "line", source: "geography",
            filter: ["in", ["get", "id"], ["literal", []]],
            paint: { "line-color": "#ffd84d", "line-width": 3 } });
          map.addLayer({ id: "reported-roads", type: "line", source: "geography",
            filter: ["in", ["get", "id"], ["literal", []]],
            paint: { "line-color": "#ff4f59", "line-width": 4, "line-opacity": 0.95 } });
          map.addLayer({
            id: "facility-dots",
            layout: { visibility: "none" },
            type: "circle",
            source: "geography",
            filter: [
              "all",
              ["==", ["geometry-type"], "Point"],
              ["==", ["get", "kind"], "site"],
            ],
            paint: {
              "circle-radius": 5,
              "circle-color": "#80c6ed",
              "circle-stroke-width": 2,
              "circle-stroke-color": "#112d41",
            },
          });
          map.addLayer({
            id: "save-limited-roads",
            type: "line",
            source: "geography",
            filter: ["==", ["get", "id"], ""],
            paint: {
              "line-color": "#f6bc5a",
              "line-width": ["interpolate", ["linear"], ["zoom"], 12, 1.5, 17, 4.5],
              "line-opacity": 0.85,
              "line-dasharray": [2, 1.5],
            },
          });
          map.addLayer({
            id: "save-blocked-roads",
            type: "line",
            source: "geography",
            filter: ["==", ["get", "id"], ""],
            paint: {
              "line-color": "#ff5468",
              "line-width": ["interpolate", ["linear"], ["zoom"], 12, 2, 17, 6],
              "line-opacity": 0.95,
            },
          });
          map.addLayer({
            id: "save-road-hit",
            type: "line",
            source: "geography",
            filter: ["==", ["get", "kind"], "road"],
            paint: { "line-color": "#000000", "line-opacity": 0, "line-width": 14 },
          });
          map.addLayer({
            id: "save-route-casing",
            type: "line",
            source: "save-routes",
            layout: { "line-cap": "round", "line-join": "round" },
            paint: { "line-color": "#04121e", "line-width": 8, "line-opacity": 0.55 },
          });
          map.addLayer({
            id: "save-route-line",
            type: "line",
            source: "save-routes",
            layout: { "line-cap": "round", "line-join": "round" },
            paint: {
              "line-color": ["get", "color"],
              "line-width": 3.5,
              "line-dasharray": [2.5, 1.5],
            },
          });
          map.addLayer({
            id: "report-halos",
            type: "circle",
            source: "reports",
            paint: {
              "circle-radius": 22,
              "circle-color": ["get", "color"],
              "circle-opacity": 0.14,
            },
          });
          map.addLayer({
            id: "report-dots",
            type: "circle",
            source: "reports",
            paint: {
              "circle-radius": 8,
              "circle-color": ["get", "color"],
              "circle-stroke-color": "#fff",
              "circle-stroke-width": 1.5,
            },
          });
          map.addLayer({
            id: "selection-dot",
            layout: { visibility: "none" },
            type: "circle",
            source: "selection",
            paint: {
              "circle-radius": 15,
              "circle-color": "#000000",
              "circle-opacity": 0,
              "circle-stroke-color": "#70e6fc",
              "circle-stroke-width": 2,
            },
          });
          map.on("click", (event) => {
            const hits = map.queryRenderedFeatures(event.point, {
              layers: [
                "report-dots",
                "local-buildings",
                "radio-alert-building",
                "selected-building",
                "facility-dots",
              ],
            });
            const report = hits.find((hit) => hit.layer.id === "report-dots");
            if (report) {
              latest.current.onReport(String(report.properties.id));
              return;
            }
            const onRoadClick = latest.current.onRoadClick;
            if (onRoadClick) {
              const road = map.queryRenderedFeatures(event.point, {
                layers: ["save-road-hit"],
              })[0];
              if (road) {
                const edgeId = String(road.properties.id);
                const blocked =
                  latest.current.operationalRoads
                    ? latest.current.operationalRoads.some(road => road.road_id === edgeId && road.status === "blocked")
                    : latest.current.save?.blocked_edge_ids.includes(edgeId) ?? false;
                onRoadClick(edgeId, !blocked);
                return;
              }
            }
            const hit = hits[0];
            const site = latest.current.geography?.sites.find(
              (site) => site.id === hit?.properties.id,
            );
            if (site) latest.current.onSite(site);
          });
          map.on("mousemove", (event) => {
            map.getCanvas().style.cursor = map.queryRenderedFeatures(
              event.point,
              {
                layers: [
                  "report-dots",
                  "local-buildings",
                  "radio-alert-building",
                  "facility-dots",
                  ...(latest.current.onRoadClick ? ["save-road-hit"] : []),
                ],
              },
            ).length
              ? "pointer"
              : "";
          });
          setReady(true);
          setFailed(false);
          setNotice("");
        });
        resize = new ResizeObserver(() => map.resize());
        resize.observe(container.current);
      } catch {
        if (!disposed) {
          setFailed(true);
          setNotice(
            "3D rendering could not start. Enable WebGL or hardware acceleration in your browser.",
          );
        }
      }
    }
    setReady(false);
    setFailed(false);
    void init();
    return () => {
      disposed = true;
      clearTimeout(timeout);
      resize?.disconnect();
      mapRef.current?.remove();
      mapRef.current = undefined;
    };
  }, [localOnly]);
  useEffect(() => {
    if (!ready) return;
    const map = mapRef.current!;
    const geometry = reportGeometry(loadedGeography.current, props.reports,
      props.save?.worksites.filter(w => w.operational_status === "resolved").map(w => w.site_id));
    const statuses = geometry.buildings;
    const colors: unknown[] = ["match", ["get", "id"]];
    for (const [id, value] of Object.entries(statuses)) colors.push(id, value.color);
    colors.push("#6893a6");
    const expression = Object.keys(statuses).length ? colors as ExpressionSpecification : "#6893a6";
    map.setPaintProperty("local-buildings", "fill-extrusion-color", expression);
    map.setPaintProperty("selected-building", "fill-extrusion-color", expression);
    map.setPaintProperty("radio-alert-building", "fill-extrusion-color", expression);
    map.setPaintProperty("facility-dots", "circle-color", expression);
    map.setPaintProperty("reported-building-outline", "line-color", expression);
    map.setFilter("reported-building-outline", ["in", ["get", "id"], ["literal", Object.keys(statuses)]]);
    map.setFilter("radio-alert-building", ["in", ["get", "id"], ["literal", Object.keys(statuses)]]);
    map.setFilter("reported-roads", ["in", ["get", "id"], ["literal", geometry.roads]]);
    const features: FeatureCollection = {
      type: "FeatureCollection",
      features: props.reports.flatMap((report) =>
        report.locations.filter(location => report.observations.length && !geometry.represented.has(location.site.id)).map((location) => ({
          type: "Feature" as const,
          geometry: {
            type: "Point" as const,
            coordinates: [location.site.longitude, location.site.latitude],
          },
          properties: {
            id: report.id,
            color: geometry.siteStatuses[location.site.id]?.color ?? "#f6bc5a",
          },
        })),
      ),
    };
    (map.getSource("reports") as GeoJSONSource)?.setData(features);
  }, [props.reports, props.droneTracking, props.save, ready]);
  useEffect(() => {
    if (!ready) return;
    const map = mapRef.current!;
    const site = props.selectedSite ?? props.selected?.locations[0]?.site;
    map.setFilter("selected-building", ["==", ["get", "id"], site?.id ?? ""]);
    (map.getSource("selection") as GeoJSONSource)?.setData(
      site
        ? {
            type: "FeatureCollection",
            features: [
              {
                type: "Feature",
                properties: {},
                geometry: {
                  type: "Point",
                  coordinates: [site.longitude, site.latitude],
                },
              },
            ],
          }
        : empty,
    );
    if (site)
      map.flyTo({
        center: [site.longitude, site.latitude],
        zoom: Math.max(map.getZoom(), 16),
        duration: 900,
        essential: false,
      });
  }, [props.selected?.id, props.selectedSite?.id, ready]);
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const save = props.save;
    const markers = unitMarkers.current;
    if (map.getLayer("save-blocked-roads")) {
      map.setFilter("save-blocked-roads", [
        "in",
        ["get", "id"],
        ["literal", props.operationalRoads?.filter(road => road.status === "blocked").map(road => road.road_id) ?? save?.blocked_edge_ids ?? []],
      ]);
      map.setFilter("save-limited-roads", [
        "in",
        ["get", "id"],
        ["literal", props.operationalRoads?.filter(road => road.status === "limited").map(road => road.road_id) ?? save?.limited_edge_ids ?? []],
      ]);
    }
    (map.getSource("save-routes") as GeoJSONSource | undefined)?.setData({
      type: "FeatureCollection",
      features: (save?.routes ?? []).map((route) => ({
        type: "Feature" as const,
        properties: {
          id: route.route_id,
          color: unitColour(route.resource_id, save!.resources),
        },
        geometry: {
          type: "LineString" as const,
          coordinates: route.path.map((point) => [point.longitude, point.latitude]),
        },
      })),
    });
    const Marker = markerCtor.current;
    if (!Marker) return;
    const live = new Set<string>();
    for (const resource of save?.resources ?? []) {
      live.add(resource.resource_id);
      const colour = unitColour(resource.resource_id, save!.resources);
      let marker = markers.get(resource.resource_id);
      if (!marker) {
        const element = document.createElement("div");
        element.className = "save-unit";
        element.innerHTML =
          `<svg class="save-unit-icon" viewBox="0 0 32 32" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 23V10a2 2 0 0 1 2-2h14v15M19 13h6l4 6v4h-3M7 23H3m8 0h10" fill="#092432"/><path d="M9 11v8m-4-4h8M22 16h3l2 4h-5z"/><circle cx="9" cy="24" r="3" fill="#092432"/><circle cx="24" cy="24" r="3" fill="#092432"/></svg><span class="save-unit-name"></span>`;
        marker = new Marker({ element, anchor: "center" })
          .setLngLat([resource.position.longitude, resource.position.latitude])
          .addTo(map) as typeof marker;
        markers.set(resource.resource_id, marker!);
      }
      const element = marker.getElement();
      element.style.setProperty("--unit-color", colour);
      element.classList.toggle("moving", resource.movement_mode !== "stationary");
      element.title = `${resource.display_name} · ${resource.status.replace("_", " ")}`;
      const name = element.querySelector(".save-unit-name");
      if (name) name.textContent = resource.display_name;
      marker.setLngLat([resource.position.longitude, resource.position.latitude]);
    }
    for (const [id, marker] of markers)
      if (!live.has(id)) {
        marker.remove();
        markers.delete(id);
      }
  }, [props.save, props.operationalRoads, ready]);
  useEffect(() => {
    const markers = unitMarkers.current;
    return () => {
      for (const marker of markers.values()) marker.remove();
      markers.clear();
    };
  }, [localOnly]);
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    map.setLayoutProperty(
      "local-buildings",
      "visibility",
      buildings ? "visible" : "none",
    );
    map.setLayoutProperty(
      "local-roads",
      "visibility",
      roads || localOnly ? "visible" : "none",
    );
    if (map.getSource("elevation"))
      map.setTerrain(terrain ? { source: "elevation", exaggeration: 1 } : null);
    map.easeTo({ pitch: is3d ? 55 : 0, duration: 500 });
  }, [buildings, roads, terrain, is3d, ready, localOnly]);
  return (
    <section
      className="map-panel"
      aria-label="Interactive 3D map of Caraballeda"
    >
      <div className="map-canvas" ref={container} />
      {ready && mapRef.current && <DroneMapOverlay map={mapRef.current} frame={props.droneTracking} />}
      {ready && mapRef.current && <ScanPhotoOverlay map={mapRef.current} reports={props.reports} onReport={props.onReport} resolvedSites={props.save?.worksites.filter(w => w.operational_status === "resolved").map(w => w.site_id)} />}
      <div className="map-location">
        <span className="map-location-icon">
          <MapPin size={19} />
        </span>
        <div>
          <strong>Caraballeda, Venezuela</strong>
          <span>
            LA GUAIRA ·{" "}
            {localOnly
              ? "LOCAL GEOGRAPHY"
              : token
                ? "SATELLITE + TERRAIN"
                : "OPEN MAP + TERRAIN"}
          </span>
        </div>
        <span className="map-3d-tag">{is3d ? "3D" : "2D"}</span>
      </div>
      <div className="map-buttons">
        <button
          title="Zoom in"
          aria-label="Zoom in"
          onClick={() => mapRef.current?.zoomIn()}
        >
          <Plus size={18} />
        </button>
        <button
          title="Zoom out"
          aria-label="Zoom out"
          onClick={() => mapRef.current?.zoomOut()}
        >
          <Minus size={18} />
        </button>
        <i />
        <button
          title="Reset map view"
          aria-label="Reset map view"
          onClick={() =>
            mapRef.current?.flyTo({ ...home, pitch: is3d ? 55 : 0 })
          }
        >
          <Crosshair size={18} />
        </button>
        <button
          title="Reset north"
          aria-label="Reset north"
          onClick={() => mapRef.current?.resetNorth()}
        >
          <Compass size={18} />
        </button>
        <button
          className={is3d ? "active" : ""}
          title="Toggle 3D"
          aria-label="Toggle 3D"
          aria-pressed={is3d}
          onClick={() => set3d(!is3d)}
        >
          <Mountain size={18} />
        </button>
        <button
          className={layersOpen ? "active" : ""}
          title="Map layers"
          aria-label="Map layers"
          aria-expanded={layersOpen}
          onClick={() => setLayersOpen(!layersOpen)}
        >
          <Layers3 size={18} />
        </button>
      </div>
      {layersOpen && (
        <div className="map-layers">
          <strong>Map layers</strong>
          <label>
            <input
              type="checkbox"
              checked={terrain}
              disabled={localOnly}
              onChange={(e) => setTerrain(e.target.checked)}
            />{" "}
            Terrain elevation
          </label>
          <label>
            <input
              type="checkbox"
              checked={roads || localOnly}
              disabled={localOnly}
              onChange={(e) => setRoads(e.target.checked)}
            />{" "}
            Road graph
          </label>
          <button onClick={() => setLocalOnly(!localOnly)}>
            {localOnly ? "Use online basemap" : "Use local geography"}
          </button>
        </div>
      )}
      {notice && (
        <div className="map-notice" role="status">
          {notice}
          {!localOnly && !failed && (
            <button onClick={() => setLocalOnly(true)}>Use local map</button>
          )}
        </div>
      )}
      {legendOpen && <div className="map-legend" aria-label="Map legend">
        <strong>MAP LEGEND</strong>
        <span>
          <i className="legend-square radio-alert" /> Yellow: awaiting scan
        </span>
        <span><i className="legend-square" style={{ background: "#fa626b", borderColor: "#fa626b" }} /> Red: people trapped</span>
        <span><i className="legend-square" style={{ background: "#29ce9e", borderColor: "#29ce9e" }} /> Green: building clear</span>
        <span><i className="legend-square" style={{ background: "#f4f7fa", borderColor: "#f4f7fa" }} /> White: rescue complete</span>
        <span>
          <i className="legend-dot amber" /> Unverified search target
        </span>
        <span><Ambulance size={16} color="#53def6" /> Ground rescue</span>
        <span><i className="legend-dot red" /> Blocked road</span>
        <span><i className="legend-dot amber" /> Limited road</span>
        <small>Sensor findings · Not a safety clearance</small>
      </div>}
      <button
        type="button"
        className="map-legend-toggle"
        aria-label={`${legendOpen ? "Hide" : "Show"} map legend`}
        aria-expanded={legendOpen}
        onClick={() => setLegendOpen(!legendOpen)}
      >
        <List size={16} />
      </button>
      <div className="map-coordinate">
        {Math.abs(center[1]).toFixed(3)}° {center[1] >= 0 ? "N" : "S"} &nbsp;{" "}
        {Math.abs(center[0]).toFixed(3)}° {center[0] >= 0 ? "E" : "W"}{" "}
        <span>WGS 84</span>
      </div>
    </section>
  );
}
