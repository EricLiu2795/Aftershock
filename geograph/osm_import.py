"""Two-pass OSM importer, preserving junctions and shape geometry separately."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import osmium
from .store import GraphStore, version


def road(tags):
    return bool(tags.get("highway")) and tags.get("highway") not in {"proposed","construction","abandoned","platform","raceway"} and tags.get("area") != "yes"


def import_osm(source, database):
    source, database = Path(source), Path(database)
    if database.exists():
        raise FileExistsError("Use a new database path; imports never overwrite assessment databases")
    counts = Counter()
    endpoints = set()
    class Junctions(osmium.SimpleHandler):
        def way(self,w):
            if road(w.tags) and len(w.nodes)>1:
                refs = [n.ref for n in w.nodes]
                counts.update(refs)
                endpoints.update((refs[0],refs[-1]))
    print("Counting road junctions...",flush=True)
    Junctions().apply_file(str(source))
    junctions = endpoints | {key for key,value in counts.items() if value>1}
    del counts
    temporary = database.with_suffix(database.suffix+".building")
    if temporary.exists():
        raise FileExistsError(temporary)
    database.parent.mkdir(parents=True,exist_ok=True)
    store = GraphStore(temporary)
    skipped = Counter()
    class Features(osmium.SimpleHandler):
        def node(self,n):
            if n.tags.get("place") and n.tags.get("name") == "Caraballeda":
                store.metadata("caraballeda_place",{"longitude":n.location.lon,"latitude":n.location.lat})
            if n.tags.get("amenity") in {"hospital","school","fire_station"} and n.location.valid():
                self.site("node",n.id,dict(n.tags),[[n.location.lon,n.location.lat]],None)

        def site(self,kind,identity,tags,points,geometry):
            lon = sum(p[0] for p in points)/len(points)
            lat = sum(p[1] for p in points)/len(points)
            facility = tags.get("amenity") if tags.get("amenity") in {"hospital","school","fire_station"} else None
            site = {"schema_version":version(),"site_id":f"osm:{kind}:{identity}",
                    "site_type":"facility" if facility else "building","display_name":tags.get("name",f"OSM {kind} {identity}"),
                    "canonical_source":"osm","canonical_source_id":f"{kind}/{identity}",
                    "centroid":{"longitude":lon,"latitude":lat},"criticality":0,
                    "metadata":{"osm_tags":tags,"criticality_assessment":"unknown","centroid_method":"vertex_average"}}
            if facility: site["facility_type"]=facility
            store.site(site,geometry)

        def way(self,w):
            tags=dict(w.tags)
            if not road(tags) and not tags.get("building"): return
            if len(w.nodes)<2 or any(not n.location.valid() for n in w.nodes):
                skipped["ways_missing_locations"]+=1; return
            refs=[n.ref for n in w.nodes]
            points=[[n.lon,n.lat] for n in w.nodes]
            if tags.get("building") and tags["building"]!="no" and refs[0]==refs[-1] and len(points)>=4:
                self.site("way",w.id,tags,points[:-1],{"type":"Polygon","coordinates":[points]})
            if road(tags):
                start=0
                for end in range(1,len(refs)):
                    if refs[end] in junctions or end==len(refs)-1:
                        a,b=f"osm:node:{refs[start]}",f"osm:node:{refs[end]}"
                        store.node(a,points[start]); store.node(b,points[end])
                        store.edge(f"osm:way:{w.id}:segment:{start}-{end}",f"osm:way:{w.id}",a,b,points[start:end+1],tags)
                        start=end

        def area(self,a):
            # Way buildings already imported; add relation multipolygons.
            if a.from_way() or not a.tags.get("building") or a.tags.get("building")=="no": return
            polygons=[]; points=[]
            for outer in a.outer_rings():
                ring=[[n.lon,n.lat] for n in outer]
                holes=[[[n.lon,n.lat] for n in inner] for inner in a.inner_rings(outer)]
                polygons.append([ring]+holes); points.extend(ring[:-1])
            if points:
                self.site("relation",a.orig_id(),dict(a.tags),points,{"type":"MultiPolygon","coordinates":polygons})
    try:
        with store.transaction():
            print("Importing roads and buildings...",flush=True)
            Features().apply_file(str(source),locations=True,idx="flex_mem")
            digest=hashlib.sha256()
            with source.open("rb") as f:
                for block in iter(lambda:f.read(1024*1024),b""): digest.update(block)
            store.metadata("source",{"file":source.name,"sha256":digest.hexdigest(),"provider":"Geofabrik/OpenStreetMap"})
            store.metadata("import_limitations",["Conditional access and turn restrictions are preserved only where present in way tags; relation turn restrictions are not evaluated.","Building centroid is a vertex average, not a verified entrance.","Unknown disaster conditions are not open roads."])
            store.metadata("skipped",dict(skipped))
        stats=store.stats()
    finally:
        store.close()
    temporary.rename(database)
    return stats
