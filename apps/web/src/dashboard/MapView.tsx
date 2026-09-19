import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import { useEffect, useRef, useState } from "react";
import type {
  Map as LibreMap,
  GeoJSONSource,
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
} from "lucide-react";
import type {
  GeographyInfo,
  IntelligenceReport,
  MapSite,
} from "../../../../contracts/intelligence";
import { reportTone } from "./model";
import "maplibre-gl/dist/maplibre-gl.css";
import "mapbox-gl/dist/mapbox-gl.css";

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
  geography?: GeographyInfo;
  reports: IntelligenceReport[];
  selected?: IntelligenceReport;
  selectedSite?: MapSite;
  onReport(id: string): void;
  onSite(site: MapSite): void;
}
export function MapView(props: Props) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<LibreMap | undefined>(undefined);
  const latest = useRef(props);
  latest.current = props;
  const [ready, setReady] = useState(false);
  const [notice, setNotice] = useState("Loading Caraballeda geography…");
  const [localOnly, setLocalOnly] = useState(false);
  const [is3d, set3d] = useState(true);
  const [buildings, setBuildings] = useState(true);
  const [roads, setRoads] = useState(false);
  const [terrain, setTerrain] = useState(true);
  const [layersOpen, setLayersOpen] = useState(false);
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
            data: "/api/geography/features",
            attribution:
              '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap contributors</a> · Geofabrik · ODbL',
          });
          map.addSource("reports", { type: "geojson", data: empty });
          map.addSource("selection", { type: "geojson", data: empty });
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
          map.addLayer({
            id: "facility-dots",
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
                "selected-building",
                "facility-dots",
              ],
            });
            const report = hits.find((hit) => hit.layer.id === "report-dots");
            if (report) latest.current.onReport(String(report.properties.id));
            else {
              const hit = hits[0];
              const site = latest.current.geography?.sites.find(
                (site) => site.id === hit?.properties.id,
              );
              if (site) latest.current.onSite(site);
            }
          });
          map.on("mousemove", (event) => {
            map.getCanvas().style.cursor = map.queryRenderedFeatures(
              event.point,
              { layers: ["report-dots", "local-buildings", "facility-dots"] },
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
    const features: FeatureCollection = {
      type: "FeatureCollection",
      features: props.reports.flatMap((report) =>
        report.locations.map((location) => ({
          type: "Feature" as const,
          geometry: {
            type: "Point" as const,
            coordinates: [location.site.longitude, location.site.latitude],
          },
          properties: {
            id: report.id,
            color: reportTone(report) === "red" ? "#ff666f" : "#f6bc5a",
          },
        })),
      ),
    };
    (map.getSource("reports") as GeoJSONSource)?.setData(features);
  }, [props.reports, ready]);
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
              checked={buildings}
              onChange={(e) => setBuildings(e.target.checked)}
            />{" "}
            3D buildings
          </label>
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
      <div className="map-legend">
        <strong>MAP LEGEND</strong>
        <span>
          <i className="legend-dot red" /> Victim / secondary hazard report
        </span>
        <span>
          <i className="legend-dot amber" /> Damage / access report
        </span>
        <span>
          <i className="legend-square" /> Building footprint
        </span>
        <small>Reports unverified · Heights may be estimated</small>
      </div>
      <div className="map-coordinate">
        {Math.abs(center[1]).toFixed(3)}° {center[1] >= 0 ? "N" : "S"} &nbsp;{" "}
        {Math.abs(center[0]).toFixed(3)}° {center[0] >= 0 ? "E" : "W"}{" "}
        <span>WGS 84</span>
      </div>
    </section>
  );
}
