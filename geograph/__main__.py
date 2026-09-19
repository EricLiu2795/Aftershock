"""Run `python -m geograph --help`. All input labels use canonical contracts."""
import argparse
import json
from pathlib import Path
from .store import GraphStore,geojson
from .drone import ingest_drone


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="command",required=True)
    imp=sub.add_parser("import-osm"); imp.add_argument("source"); imp.add_argument("database")
    for name in ("stats","ingest","export","assess-route"):
        cmd=sub.add_parser(name); cmd.add_argument("database")
        if name=="ingest":
            cmd.add_argument("kind",choices=["observation","incident","site","snapshot","drone","road-assessment","road-state"])
            cmd.add_argument("input"); cmd.add_argument("--site-id")
        elif name=="export":
            cmd.add_argument("output"); cmd.add_argument("--bbox",nargs=4,type=float,required=True,metavar=("W","S","E","N"))
        elif name=="assess-route":
            cmd.add_argument("edge_ids",nargs="+"); cmd.add_argument("--profile",choices=["foot","vehicle"],default="foot")
            cmd.add_argument("--at",required=True); cmd.add_argument("--max-age-s",type=float,default=3600)
    args=p.parse_args()
    if args.command=="import-osm":
        from .osm_import import import_osm
        print(json.dumps(import_osm(args.source,args.database),indent=2)); return
    if not Path(args.database).is_file(): p.error("Database does not exist; import a map first")
    store=GraphStore(args.database)
    try:
        if args.command=="stats": print(json.dumps(store.stats(),indent=2))
        elif args.command=="ingest":
            raw=Path(args.input).read_text()
            if Path(args.input).suffix==".jsonl":
                values=[json.loads(line) for line in raw.splitlines() if line.strip()]
            else:
                data=json.loads(raw)
                values=data if isinstance(data,list) else [data]
            with store.transaction():
                for value in values:
                    if args.kind=="drone": ingest_drone(store,value,args.site_id)
                    elif args.kind=="observation": store.observation(value,args.site_id)
                    else: getattr(store,args.kind.replace("-","_"))(value)
            print(json.dumps(store.stats(),indent=2))
        elif args.command=="export":
            graph=store.export(args.bbox)
            out=Path(args.output); out.parent.mkdir(parents=True,exist_ok=True)
            out.write_text(json.dumps(graph,ensure_ascii=False,allow_nan=False,indent=2)+"\n")
            out.with_suffix(".geojson").write_text(json.dumps(geojson(graph),ensure_ascii=False,allow_nan=False)+"\n")
            print(json.dumps({"nodes":len(graph["nodes"]),"edges":len(graph["edges"]),"sites":len(graph["sites"])}))
        else:
            print(json.dumps(store.assess_route(args.edge_ids,args.profile,args.at,args.max_age_s),indent=2))
    finally: store.close()


if __name__=="__main__": main()
