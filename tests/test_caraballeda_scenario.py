"""Caraballeda Scenario Pack v0: identity freeze, contracts, and graph membership."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import re
import unittest

from ingestion.validation import validator


ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "data" / "scenarios" / "caraballeda"
GRAPH = ROOT / "data" / "graph_exports" / "caraballeda.json"
START = datetime.fromisoformat("2026-09-19T14:00:00+00:00")

FROZEN = {
    "w1_site": "osm:way:1533952351",
    "w1_arrival": "osm:node:925977824",
    "staging": "osm:node:925978252",
    "fire_station": "osm:node:13998439701",
    "r1": "osm:way:376529129:segment:1-12",
    "r2": "osm:way:79138347:segment:0-1",
    "r3": "osm:way:1205110985:segment:0-2",
}

DISCLAIMER = (
    "The map, road network, building geometry, and canonical Site identities are "
    "derived from real geospatial data. Victims, operational incidents, rescue "
    "resources, report timing, and active road disruptions are simulated for the "
    "Aftershock demonstration."
)


def load(name):
    return json.loads((PACK / name).read_text(encoding="utf-8"))


def dump_walk(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key, item
            yield from dump_walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from dump_walk(item)


class ScenarioPackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = load("scenario.json")
        cls.sites = load("active_sites.json")
        cls.truth = load("scenario_ground_truth.json")
        cls.events = load("scenario_events.json")
        cls.assets = load("verification_assets.json")
        cls.worksites = cls.sites["active_worksites"]
        cls.event_list = cls.events["events"]
        cls.asset_list = cls.assets["assets"]

    def test_pack_files_exist(self):
        for name in ("scenario.json", "active_sites.json", "scenario_ground_truth.json",
                     "scenario_events.json", "verification_assets.json", "README.md"):
            self.assertTrue((PACK / name).is_file(), name)
        self.assertTrue((PACK / "assets" / "drone").is_dir())
        self.assertEqual(
            self.manifest["pack_files"]["verification_assets"],
            "data/scenarios/caraballeda/verification_assets.json",
        )

    def test_disclaimer_present(self):
        self.assertEqual(self.manifest["provenance"]["disclaimer"], DISCLAIMER)
        readme = (PACK / "README.md").read_text(encoding="utf-8")
        self.assertIn(DISCLAIMER, readme)
        self.assertIn("Never copy `scenario_ground_truth.json` into `OperationalBeliefState`.", readme)

    def test_graph_is_referenced_not_duplicated(self):
        self.assertEqual(self.manifest["graph"]["labeled_graph"], "data/graph_exports/caraballeda.json")
        self.assertEqual(self.manifest["bbox"], [-66.87, 10.59, -66.81, 10.64])
        self.assertFalse((PACK / "caraballeda.json").exists())

    def test_baseline_road_policy_is_simulation_only(self):
        policy = self.manifest["baseline_road_policy"]
        self.assertEqual(policy["policy"], "assume_open_unless_overridden")
        self.assertTrue(policy["does_not_mutate_osm"])
        self.assertEqual(policy["applies_to"], "simulation_only")

    def test_no_vehicle_subclasses(self):
        self.assertTrue(self.manifest["vehicle_policy"]["no_light_heavy_split"])
        self.assertEqual(self.manifest["vehicle_policy"]["classes"], ["generic_rescue_team"])
        for resource in self.manifest["resources"]:
            self.assertNotIn("light_truck", resource.get("capability_tags", []))
            self.assertNotIn("heavy_truck", resource.get("capability_tags", []))
            self.assertNotIn("heavy_rescue", resource.get("capability_tags", []))

    def test_unique_worksite_and_site_ids(self):
        keys = [item["worksite_key"] for item in self.worksites]
        site_ids = [item["site"]["site_id"] for item in self.worksites]
        arrivals = [item["arrival_node_id"] for item in self.worksites]
        self.assertEqual(keys, ["W1", "W2", "W3", "W4", "W5", "W6", "W7", "W8"])
        self.assertEqual(len(set(keys)), 8)
        self.assertEqual(len(set(site_ids)), 8)
        self.assertEqual(len(set(arrivals)), 8)

    def test_unique_resource_ids(self):
        resource_ids = [item["resource_id"] for item in self.manifest["resources"]]
        movement_ids = [item["resource_id"] for item in self.manifest["initial_movement_states"]]
        self.assertEqual(resource_ids, ["rescue-alpha", "rescue-bravo", "rescue-charlie", "drone-1", "drone-2"])
        self.assertEqual(len(set(resource_ids)), 5)
        self.assertEqual(set(resource_ids), set(movement_ids))
        self.assertEqual(
            {item["display_name"] for item in self.manifest["resources"]},
            {"Rescue Team Alpha", "Rescue Team Bravo", "Rescue Team Charlie", "Drone Unit 1", "Drone Unit 2"},
        )

    def test_frozen_identities(self):
        w1 = next(item for item in self.worksites if item["worksite_key"] == "W1")
        self.assertEqual(w1["site"]["site_id"], FROZEN["w1_site"])
        self.assertEqual(w1["arrival_node_id"], FROZEN["w1_arrival"])
        self.assertEqual(self.manifest["rescue_base"]["site"]["site_id"], FROZEN["fire_station"])
        self.assertEqual(self.manifest["rescue_base"]["staging_node_id"], FROZEN["staging"])
        disruptions = {item["disruption_id"]: item for item in self.manifest["road_disruptions"]}
        self.assertEqual(disruptions["R1"]["edge_id"], FROZEN["r1"])
        self.assertEqual(disruptions["R2"]["edge_id"], FROZEN["r2"])
        self.assertEqual(disruptions["R3"]["edge_id"], FROZEN["r3"])
        self.assertEqual(disruptions["R1"]["initial_status"], "open")
        self.assertEqual(disruptions["R1"]["later_status"], "blocked")
        self.assertEqual(disruptions["R2"]["initial_status"], "limited")
        self.assertEqual(disruptions["R2"]["travel_cost_multiplier"], 2.0)
        self.assertEqual(disruptions["R3"]["initial_status"], "limited")
        self.assertEqual(disruptions["R3"]["travel_cost_multiplier"], 1.3)

    def test_contract_payloads(self):
        validator("site.schema.json").validate(self.manifest["rescue_base"]["site"])
        for item in self.worksites:
            validator("site.schema.json").validate(item["site"])
        for resource in self.manifest["resources"]:
            validator("resource.schema.json").validate(resource)
        for movement in self.manifest["initial_movement_states"]:
            validator("movement.schema.json").validate(movement)
        for state in self.manifest["initial_road_states"]:
            validator("road_state.schema.json").validate(state)
            self.assertIn(state["status"], {"open", "limited", "blocked"})

        schema = {
            "social": "raw_social_input.schema.json",
            "radio": "raw_radio_input.schema.json",
            "drone": "drone_observation_input.schema.json",
        }
        for event in self.event_list:
            validator(schema[event["source_type"]]).validate(event["raw_input"])

    def test_events_are_deterministic_and_ordered(self):
        ids = [event["event_id"] for event in self.event_list]
        times = [event["scenario_time_s"] for event in self.event_list]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(times, sorted(times))
        self.assertTrue(8 <= len(self.event_list) <= 13)
        self.assertIn("E05", ids)
        self.assertIn("E12", ids)
        self.assertEqual(times[0], 20)
        self.assertLessEqual(times[-1], 300)
        for previous, current in zip(times, times[1:]):
            self.assertLess(previous, current)

        for event in self.event_list:
            expected = START + timedelta(seconds=event["scenario_time_s"])
            actual = datetime.fromisoformat(event["raw_input"]["timestamp"].replace("Z", "+00:00"))
            self.assertEqual(actual, expected)
            self.assertEqual(actual.tzinfo, timezone.utc)

    def test_event_referents_are_known(self):
        site_ids = {item["site"]["site_id"] for item in self.worksites}
        worksite_keys = {item["worksite_key"] for item in self.worksites}
        truth_ids = {item["ground_truth_id"] for item in self.truth["worksites"] + self.truth["roads"]}
        edge_ids = {item["edge_id"] for item in self.manifest["road_disruptions"]}
        for event in self.event_list:
            referent = event["intended_referent"]
            self.assertIn(referent["worksite_key"], worksite_keys)
            self.assertIn(referent["site_id"], site_ids)
            self.assertIn(event["hidden_truth_linkage"], truth_ids)
            if referent.get("road_edge_id"):
                self.assertIn(referent["road_edge_id"], edge_ids)
            if referent.get("disruption_id"):
                self.assertIn(referent["disruption_id"], {"R1", "R2", "R3"})

        truth_sites = {item["worksite_key"]: item["site_id"] for item in self.truth["worksites"]}
        live_sites = {item["worksite_key"]: item["site"]["site_id"] for item in self.worksites}
        self.assertEqual(truth_sites, live_sites)

    def test_r1_event_encodes_same_target_reroute(self):
        event = next(item for item in self.event_list if item["event_id"] == "E10")
        self.assertEqual(event["intended_referent"]["road_edge_id"], FROZEN["r1"])
        transition = event["expected_state_transition"]
        self.assertEqual(transition["resource_id"], "rescue-bravo")
        self.assertEqual(transition["current_worksite_key"], "W1")
        self.assertTrue(transition["objective_unchanged"])
        self.assertFalse(transition["requires_human_approval"])
        self.assertEqual(transition["old_route_status"], "superseded")
        self.assertEqual(transition["recompute_from"], "MovementState.position")
        road = event["expected_downstream_interpretation"]["road_state"]
        self.assertEqual(road["status"], "blocked")
        validator("road_state.schema.json").validate({
            "schema_version": "2.1",
            "road_id": road["road_id"],
            "status": road["status"],
            "travel_cost_multiplier": road["travel_cost_multiplier"],
            "updated_at": event["raw_input"]["timestamp"],
        })

    def test_simulated_operational_data_is_not_labeled_historical(self):
        self.assertTrue(self.sites["operational_incident_data_simulated"])
        self.assertTrue(self.truth["simulated"])
        self.assertTrue(self.truth["hidden_from_operational_belief_state"])
        for item in self.worksites:
            self.assertTrue(item["operational_incident_data_simulated"])
            self.assertTrue(item["historical_context"]["not_simulated_operational_truth"])
        for event in self.event_list:
            self.assertEqual(event["provenance"], "simulated")
            metadata = event["raw_input"]["metadata"]
            self.assertEqual(metadata["provenance"], "simulated")
            self.assertTrue(metadata["not_historical_casualty_record"])
            blob = json.dumps(event["raw_input"])
            self.assertIsNone(re.search(r"historical (fact|casualty|victim|road damage)", blob, re.I))
        for row in self.truth["worksites"] + self.truth["roads"]:
            self.assertTrue(row["simulated"])

    def test_ground_truth_not_embedded_in_command_manifest_resources(self):
        for _, value in dump_walk(self.manifest["resources"]):
            self.assertNotEqual(value, "scenario_ground_truth.json")
        self.assertNotIn("worksites", self.manifest)
        self.assertNotIn("incidents", self.manifest)

    def test_name_identity_is_not_invented(self):
        policy = self.sites["name_identity_policy"]
        self.assertIn("Perlamar is not treated as Residencias Las Perlas", policy)
        self.assertIn("Ritamar Palace is not treated as Rita Sol Palace or Rita Luna Palaca", policy)
        w6 = next(item for item in self.worksites if item["worksite_key"] == "W6")
        self.assertEqual(w6["site"]["site_id"], "osm:way:953861531")
        self.assertNotIn("Perlamar", w6["aliases"])
        w8 = next(item for item in self.worksites if item["worksite_key"] == "W8")
        self.assertEqual(w8["site"]["display_name"], "Rita Luna Palaca")
        self.assertNotIn("Ritamar Palace", w8["aliases"])

    def test_starting_positions_use_staging_node(self):
        staging = self.manifest["rescue_base"]["staging_position"]
        for movement in self.manifest["initial_movement_states"]:
            self.assertEqual(movement["position"], staging)
            self.assertEqual(movement["movement_mode"], "stationary")
            self.assertIsNone(movement["route_id"])

    def test_readme_mentions_verification_assets(self):
        readme = (PACK / "README.md").read_text(encoding="utf-8")
        self.assertIn("drone-verification", readme.lower().replace(" ", "-"))
        self.assertIn("verification_assets.json", readme)
        self.assertIn("simulated renders tied to real OSM sites", readme)
        self.assertIn("not operational truth by themselves", readme.lower().replace("*", ""))
        self.assertIn("revealed_asset_ids", readme)
        self.assertIn("E05", readme)
        self.assertIn("E12", readme)
        self.assertIn("va-w2-drone-oblique", readme)
        self.assertIn("va-w7-drone-topdown", readme)
        self.assertNotRegex(readme, r"historical disaster (photo|imagery) of the 2026")


class VerificationAssetTests(unittest.TestCase):
    SOURCE_KINDS = {"real_photo", "satellite_crop", "simulated_render"}
    FILE_STATUSES = {"present", "placeholder"}
    REQUIRED_FIELDS = {
        "asset_id", "site_id", "worksite_key", "asset_type", "view_type",
        "source_kind", "path", "caption", "usable_for_verification",
        "reveal_condition", "provenance", "notes", "file_status", "placeholder",
    }

    @classmethod
    def setUpClass(cls):
        cls.manifest = load("scenario.json")
        cls.sites = load("active_sites.json")
        cls.events = load("scenario_events.json")
        cls.assets_doc = load("verification_assets.json")
        cls.worksites = {item["worksite_key"]: item for item in cls.sites["active_worksites"]}
        cls.asset_list = cls.assets_doc["assets"]
        cls.assets_by_id = {item["asset_id"]: item for item in cls.asset_list}
        cls.event_list = cls.events["events"]
        cls.events_by_id = {item["event_id"]: item for item in cls.event_list}

    def test_asset_ids_are_unique(self):
        ids = [item["asset_id"] for item in self.asset_list]
        self.assertTrue(ids)
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_asset_has_required_fields(self):
        for asset in self.asset_list:
            missing = self.REQUIRED_FIELDS - set(asset)
            self.assertFalse(missing, f"{asset.get('asset_id')}: missing {missing}")
            self.assertEqual(asset["asset_type"], "image")
            self.assertIn(asset["source_kind"], self.SOURCE_KINDS)
            self.assertIn(asset["file_status"], self.FILE_STATUSES)
            self.assertIsInstance(asset["usable_for_verification"], bool)
            self.assertIsInstance(asset["placeholder"], bool)
            self.assertIsInstance(asset["reveal_condition"]["event_ids"], list)
            self.assertTrue(asset["provenance"]["not_historical_disaster_imagery"])
            self.assertTrue(asset["provenance"]["not_operational_truth"])

    def test_every_asset_references_a_valid_active_site(self):
        for asset in self.asset_list:
            worksite = self.worksites[asset["worksite_key"]]
            self.assertEqual(asset["site_id"], worksite["site"]["site_id"], asset["asset_id"])
            self.assertIn(asset["asset_id"], worksite["verification_asset_ids"])

    def test_site_verification_ids_exist_and_cover_required_sites(self):
        asset_ids = set(self.assets_by_id)
        for worksite in self.worksites.values():
            self.assertIn("verification_asset_ids", worksite)
            for asset_id in worksite["verification_asset_ids"]:
                self.assertIn(asset_id, asset_ids)
        for key in ("W2", "W7"):
            self.assertTrue(self.worksites[key]["verification_asset_ids"], key)

    def test_event_referenced_asset_ids_exist(self):
        for event in self.event_list:
            for asset_id in event.get("revealed_asset_ids", []):
                self.assertIn(asset_id, self.assets_by_id, f"{event['event_id']} -> {asset_id}")
                asset = self.assets_by_id[asset_id]
                self.assertEqual(asset["site_id"], event["intended_referent"]["site_id"])
                self.assertEqual(asset["worksite_key"], event["intended_referent"]["worksite_key"])
                self.assertIn(event["event_id"], asset["reveal_condition"]["event_ids"])

    def test_paths_exist_or_placeholder_is_explicit(self):
        drone_dir = PACK / "assets" / "drone"
        self.assertTrue(drone_dir.is_dir())
        for asset in self.asset_list:
            path = PACK / asset["path"]
            self.assertTrue(str(asset["path"]).startswith("assets/drone/"), asset["asset_id"])
            if path.is_file():
                self.assertGreater(path.stat().st_size, 0, asset["asset_id"])
                self.assertIn(asset["source_kind"], self.SOURCE_KINDS)
            else:
                self.assertEqual(asset["file_status"], "placeholder", asset["asset_id"])
                self.assertTrue(asset["placeholder"], asset["asset_id"])
                self.assertEqual(asset["source_kind"], "simulated_render", asset["asset_id"])
                self.assertIn("placeholder", asset["notes"].lower())
            if asset["file_status"] == "present":
                self.assertTrue(path.is_file(), asset["asset_id"])
                self.assertFalse(asset["placeholder"])

    def test_e05_and_e12_reveal_valid_assets(self):
        e05 = self.events_by_id["E05"]
        e12 = self.events_by_id["E12"]
        self.assertEqual(e05["source_type"], "drone")
        self.assertEqual(e12["source_type"], "drone")
        self.assertEqual(e05["revealed_asset_ids"], ["va-w2-drone-oblique"])
        self.assertEqual(e12["revealed_asset_ids"], ["va-w7-drone-topdown"])
        self.assertEqual(e05["verification_summary"]["worksite_key"], "W2")
        self.assertEqual(e12["verification_summary"]["worksite_key"], "W7")
        self.assertEqual(e05["raw_input"]["metadata"]["verification_asset_ids"], ["va-w2-drone-oblique"])
        self.assertEqual(e12["raw_input"]["metadata"]["verification_asset_ids"], ["va-w7-drone-topdown"])
        self.assertTrue(e05["raw_input"]["metadata"]["verification_image_is_simulated"])
        self.assertTrue(e12["raw_input"]["metadata"]["verification_image_is_simulated"])
        self.assertEqual(self.assets_by_id["va-w2-drone-oblique"]["imagery_reference"],
                         e05["raw_input"]["imagery_reference"])
        self.assertEqual(self.assets_by_id["va-w7-drone-topdown"]["imagery_reference"],
                         e12["raw_input"]["imagery_reference"])

    def test_simulated_images_are_not_claimed_as_historical_photos(self):
        blob = json.dumps(self.assets_doc).lower()
        self.assertNotIn("historical disaster photography", blob.replace("not historical disaster photography", ""))
        for asset in self.asset_list:
            if asset["source_kind"] == "simulated_render":
                self.assertTrue(asset["provenance"]["not_historical_disaster_imagery"])
                self.assertIn("not historical disaster photography", asset["caption"].lower())
            if asset["placeholder"] or asset["file_status"] == "placeholder":
                self.assertEqual(asset["source_kind"], "simulated_render")
                self.assertNotEqual(asset["source_kind"], "real_photo")


@unittest.skipUnless(GRAPH.is_file(), "Caraballeda graph export is not present; generate data/graph_exports/caraballeda.json")
class ScenarioGraphMembershipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = json.loads(GRAPH.read_text(encoding="utf-8"))
        cls.sites = load("active_sites.json")
        cls.manifest = load("scenario.json")
        cls.assets = load("verification_assets.json")
        cls.node_ids = {node["node_id"] for node in cls.graph["nodes"]}
        cls.edge_ids = {edge["edge_id"] for edge in cls.graph["edges"]}
        cls.site_ids = {item["site"]["site_id"] for item in cls.graph["sites"]}

    def test_export_bbox_matches_pack(self):
        self.assertEqual(self.graph["bbox"], self.manifest["bbox"])

    def test_active_sites_exist(self):
        self.assertIn(FROZEN["fire_station"], self.site_ids)
        for item in self.sites["active_worksites"]:
            self.assertIn(item["site"]["site_id"], self.site_ids, item["worksite_key"])

    def test_arrival_and_staging_nodes_exist(self):
        self.assertIn(FROZEN["staging"], self.node_ids)
        for item in self.sites["active_worksites"]:
            self.assertIn(item["arrival_node_id"], self.node_ids, item["worksite_key"])

    def test_road_edges_exist(self):
        for edge_id in (FROZEN["r1"], FROZEN["r2"], FROZEN["r3"]):
            self.assertIn(edge_id, self.edge_ids, edge_id)

    def test_verification_asset_site_ids_exist_in_graph(self):
        for asset in self.assets["assets"]:
            self.assertIn(asset["site_id"], self.site_ids, asset["asset_id"])


if __name__ == "__main__":
    unittest.main()
