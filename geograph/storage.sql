CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS nodes (node_id TEXT PRIMARY KEY, lon REAL NOT NULL, lat REAL NOT NULL);
CREATE TABLE IF NOT EXISTS edges (
 edge_id TEXT PRIMARY KEY, road_id TEXT NOT NULL,
 source TEXT NOT NULL REFERENCES nodes(node_id), target TEXT NOT NULL REFERENCES nodes(node_id),
 length_m REAL NOT NULL CHECK(length_m>=0), path TEXT NOT NULL, tags TEXT NOT NULL,
 minlon REAL NOT NULL,minlat REAL NOT NULL,maxlon REAL NOT NULL,maxlat REAL NOT NULL,kind TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS edges_road ON edges(road_id);
CREATE INDEX IF NOT EXISTS edges_bounds ON edges(minlon,maxlon,minlat,maxlat);
CREATE TABLE IF NOT EXISTS sites (site_id TEXT PRIMARY KEY, payload TEXT NOT NULL, geometry TEXT, lon REAL NOT NULL,lat REAL NOT NULL);
CREATE INDEX IF NOT EXISTS sites_location ON sites(lon,lat);
CREATE TABLE IF NOT EXISTS records (
 kind TEXT NOT NULL,id TEXT NOT NULL,site_id TEXT REFERENCES sites(site_id),road_id TEXT,
 timestamp TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(kind,id));
CREATE INDEX IF NOT EXISTS records_site ON records(site_id,kind);
CREATE INDEX IF NOT EXISTS records_road ON records(road_id,kind);
