"""Run `python -m saveplanner serve` to start the operational map.

Commands:
    serve    graph + planner + live map on a local port
    route    shortest passable route between two junctions, as JSON
    plan     one Save-planner pass printed as SaveDecision records
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .roadgraph import RoadGraph
from .runtime import SavePlannerRuntime
from .scenario import build
from .server import serve

DEFAULT_GRAPH = Path("data/graph_exports/caraballeda.json")


def load(graph_path):
    path = Path(graph_path)
    if not path.is_file():
        raise SystemExit(f"Graph export not found: {path}\n"
                         "Produce one with: python -m geograph export <db> <out.json> --bbox W S E N")
    graph = RoadGraph.load(path)
    return graph, build(path, graph)


def load_sites(graph_path):
    """Buildings from the export. They carry no edges; routing snaps them to a road."""
    raw = json.loads(Path(graph_path).read_text(encoding="utf-8"))
    return [entry["site"] for entry in raw["sites"]]


def find_site(sites, needle):
    exact = [s for s in sites if s["site_id"] == needle]
    if exact:
        return exact[0]
    matches = [s for s in sites
               if needle.casefold() in (s.get("display_name") or "").casefold()]
    if not matches:
        raise SystemExit(f"No building matches {needle!r}")
    if len(matches) > 1:
        # Names repeat (chains, unnamed blocks), so offer the site ids that disambiguate.
        listing = "\n".join(
            f"  {m['site_id']:<26} {m.get('display_name')}  "
            f"({m['centroid']['latitude']:.5f}, {m['centroid']['longitude']:.5f})"
            for m in matches[:8])
        raise SystemExit(f"{needle!r} matches {len(matches)} buildings; use a site id:\n{listing}")
    return matches[0]


def main():
    # Venezuelan street names are UTF-8; the Windows console defaults to cp1252.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="saveplanner", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--graph", default=str(DEFAULT_GRAPH), help="geograph export JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    web = sub.add_parser("serve", help="run the live operational map")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8765)

    path = sub.add_parser("route", help="route between two graph junctions")
    path.add_argument("origin")
    path.add_argument("destination")
    path.add_argument("--block", nargs="*", default=[], help="edge IDs to treat as blocked")
    path.add_argument("--verbose", action="store_true", help="print the full node/edge path")

    between = sub.add_parser("between", help="route between two buildings by name or site id")
    between.add_argument("origin", help="site id, or part of a building name")
    between.add_argument("destination", help="site id, or part of a building name")
    between.add_argument("--block", nargs="*", default=[], help="edge IDs to treat as blocked")

    sub.add_parser("plan", help="print one Save-planner pass")
    sub.add_parser("verify", help="acceptance checks against the stated requirements")

    args = parser.parse_args()

    if args.command == "verify":
        from .verify import run
        raise SystemExit(1 if run(args.graph) else 0)

    graph, scenario = load(args.graph)

    if args.command == "route":
        for edge_id in args.block:
            if edge_id not in graph.edges:
                raise SystemExit(f"Unknown edge: {edge_id}")
            graph.set_blocked(edge_id, True)
        found = graph.route(args.origin, args.destination)
        if found is None:
            raise SystemExit("No passable route: every approach is blocked.")
        if args.verbose:
            print(json.dumps(found, indent=2))
            return
        streets = []
        for edge_id in found["edge_ids"]:
            name = graph.edges[edge_id].name
            if not streets or streets[-1] != name:
                streets.append(name)
        print(f"distance   {found['distance_m'] / 1000:.2f} km")
        print(f"travel     {found['seconds'] / 60:.1f} min")
        print(f"segments   {len(found['edge_ids'])} edges, {len(found['path'])} shape points")
        if args.block:
            print(f"avoiding   {len(args.block)} blocked edge(s): "
                  f"{'yes' if not set(args.block) & set(found['edge_ids']) else 'NO - BUG'}")
        print("via        " + " -> ".join(streets[:12]) + (" -> ..." if len(streets) > 12 else ""))
        return

    if args.command == "between":
        sites = load_sites(args.graph)
        origin = find_site(sites, args.origin)
        destination = find_site(sites, args.destination)
        for edge_id in args.block:
            graph.set_blocked(edge_id, True)
        found = graph.route_points(
            [origin["centroid"]["longitude"], origin["centroid"]["latitude"]],
            [destination["centroid"]["longitude"], destination["centroid"]["latitude"]])
        print(f"from  {origin['display_name']}  ({origin['site_id']})")
        print(f"to    {destination['display_name']}  ({destination['site_id']})")
        if found is None:
            raise SystemExit("\nNo route: no passable road within walking range, "
                             "or the road network between them is severed.")
        print(f"\n  walk to road   {found['entry_m']:6.0f} m   (buildings are not on the road graph)")
        print(f"  drive          {found['distance_m']:6.0f} m   {found['seconds'] / 60:.1f} min"
              f"   over {len(found['edge_ids'])} road segments")
        print(f"  walk to door   {found['exit_m']:6.0f} m")
        return

    runtime = SavePlannerRuntime(graph, scenario)
    if args.command == "plan":
        print(json.dumps(list(runtime.save_decisions.values()), indent=2))
        return
    serve(runtime, graph, args.host, args.port)


if __name__ == "__main__":
    main()
