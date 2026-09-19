"""Run `python -m saveplanner serve` to drive the Save layer.

Commands:
    serve    replay the frozen Caraballeda timeline, served to the dashboard
    plan     one Save pass at a scenario event, printed as SaveDecision records
    route    shortest passable route between two graph junctions
    verify   acceptance checks against the frozen scenario
"""
from __future__ import annotations

import argparse
import json
import sys

from .demo import SaveDemo
from .execution import ScenarioExecutionIndex
from .roadgraph import RoadGraph
from .server import serve


def main():
    # Venezuelan street names are UTF-8; the Windows console defaults to cp1252.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="saveplanner", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    web = sub.add_parser("serve", help="standalone Save harness on :8765 (not used by npm run dev)")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8765)

    plan = sub.add_parser("plan", help="print one Save pass at a scenario event")
    plan.add_argument("--at", default="E07", help="scenario event id, e.g. E07")

    path = sub.add_parser("route", help="route between two graph junctions")
    path.add_argument("origin")
    path.add_argument("destination")
    path.add_argument("--block", nargs="*", default=[], help="edge IDs to treat as BLOCKED")
    path.add_argument("--limit", nargs="*", default=[], help="edge IDs to treat as LIMITED")
    path.add_argument("--multiplier", type=float, default=2.0,
                      help="travel_cost_multiplier applied to --limit edges")
    path.add_argument("--verbose", action="store_true")

    sub.add_parser("verify", help="acceptance checks against the frozen scenario")
    args = parser.parse_args()

    if args.command == "verify":
        from .verify import run
        raise SystemExit(1 if run() else 0)

    if args.command == "route":
        index = ScenarioExecutionIndex.load()
        graph = RoadGraph.load(index.graph_path)
        for edge_id in args.block:
            graph.set_road_status(edge_id, "blocked")
        for edge_id in args.limit:
            graph.set_road_status(edge_id, "limited", args.multiplier)
        found = graph.route(args.origin, args.destination)
        if found is None:
            raise SystemExit("No passable route: every approach is BLOCKED.")
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
        print(f"segments   {len(found['edge_ids'])} edges")
        if args.block:
            clear = not set(args.block) & set(found["edge_ids"])
            print(f"avoiding   {len(args.block)} BLOCKED edge(s): {'yes' if clear else 'NO - BUG'}")
        if args.limit:
            used = set(args.limit) & set(found["edge_ids"])
            print(f"limited    {len(used)} of {len(args.limit)} LIMITED edge(s) still used "
                  f"(traversable at x{args.multiplier})")
        print("via        " + " -> ".join(streets[:12]) + (" -> ..." if len(streets) > 12 else ""))
        return

    demo = SaveDemo()
    if args.command == "plan":
        demo.jump_to(args.at)
        print(f"scenario {demo.index.scenario_id} at event {demo.event_id}\n")
        for worksite_id, decision in sorted(demo.controller.decisions.items(),
                                            key=lambda kv: -kv[1]["save_priority"]):
            best = max(decision["candidate_assignments"],
                       key=lambda c: c["assignment_utility"], default=None)
            print(f"  {demo.controller.label(demo.belief, worksite_id):<4} "
                  f"prio {decision['save_priority']:.3f}  task {decision['recommended_task']:<16} "
                  f"best {best['resource_id'] if best else 'none':<16} "
                  f"util {best['assignment_utility']:.3f}" if best else "  no route")
        return
    serve(demo, args.host, args.port)


if __name__ == "__main__":
    main()
