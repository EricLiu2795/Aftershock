"""Real Caraballeda Scenario Pack → OperationalBeliefState integration."""

from pathlib import Path
import json
import unittest

from operational.belief_state import apply_observation, empty_state
from operational.ids import evidence_id
from operational.location_resolver import LocationResolver
from operational.site_registry import ScenarioPackSiteRegistry
from tests.operational_fixtures import T0, make_observation


ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "data" / "scenarios" / "caraballeda"
ACTIVE_SITES = PACK / "active_sites.json"
SCENARIO_EVENTS = PACK / "scenario_events.json"

W1 = "osm:way:1533952351"
W2 = "osm:way:1533952352"
W4 = "osm:way:1533564319"
W6 = "osm:way:953861531"
W7 = "osm:node:13976280529"


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def event_by_id(events, event_id):
    return next(item for item in events["events"] if item["event_id"] == event_id)


def worksite_for(state, site_id):
    return next(item for item in state.belief["worksites"] if item["site_id"] == site_id)


def incidents_for(state, site_id, incident_type=None):
    items = [item for item in state.belief["incidents"] if item["site_id"] == site_id]
    if incident_type is not None:
        items = [item for item in items if item["incident_type"] == incident_type]
    return items


class CaraballedaOperationalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sites_doc = load_json(ACTIVE_SITES)
        cls.events_doc = load_json(SCENARIO_EVENTS)
        cls.registry = ScenarioPackSiteRegistry(ACTIVE_SITES)
        cls.resolver = LocationResolver()

    def setUp(self):
        self.state = empty_state(T0)

    def _event(self, event_id):
        return event_by_id(self.events_doc, event_id)

    def _resolve_text(self, raw_text, **kwargs):
        observation = make_observation("obs-resolve", raw_text=raw_text, **kwargs)
        result = self.resolver.resolve(observation, self.registry)
        self.assertIsNone(observation["location"]["resolved_site_id"])
        return result

    def test_registry_loads_nested_sites_without_rewriting_osm_criticality(self):
        self.assertIn("active_worksites", self.sites_doc)
        self.assertNotIn("sites", self.sites_doc)
        site = self.registry.get_site(W1)
        overlay = self.registry.get_scenario_overlay(W1)
        self.assertEqual(site["site_id"], W1)
        self.assertEqual(site["criticality"], 0)
        self.assertEqual(site["metadata"]["criticality_assessment"], "unknown")
        self.assertEqual(overlay["worksite_key"], "W1")
        self.assertEqual(overlay["scenario_criticality"], 0.78)
        self.assertTrue(overlay["not_osm_criticality"])
        self.assertEqual(site["metadata"]["scenario_operational_overlay"]["scenario_criticality"], 0.78)
        self.assertIsNone(self.registry.get_site("W1"))
        self.assertEqual(self.registry.find_normalized_alias("W1"), [])

    def test_plain_residencias_caribe_remains_ambiguous(self):
        result = self._resolve_text("Residencias Caribe")
        self.assertEqual(result["resolution_status"], "ambiguous")
        self.assertIsNone(result["resolved_site_id"])
        self.assertEqual(set(result["candidate_site_ids"]), {W1, W2})

    def test_w1_context_phrases_resolve_without_intended_referent(self):
        e01 = self._event("E01")["raw_input"]["text"]
        e02 = self._event("E02")["raw_input"]["transcript"]
        self.assertIn("Residencias Caribe on Avenida Principal de Caribe", e01)
        self.assertIn("Residencias Caribe, Avenida Principal de Caribe", e02)
        self.assertIn("north side", e02)
        for text in (
            e01,
            e02,
            "Residencias Caribe on Avenida Principal de Caribe",
            "Residencias Caribe Avenida Principal de Caribe",
            "Caribe A",
        ):
            result = self._resolve_text(text)
            self.assertEqual(result["resolved_site_id"], W1, text)
            self.assertNotIn("intended_referent", result["metadata"])

    def test_w2_context_phrases_resolve_without_intended_referent(self):
        e03 = self._event("E03")["raw_input"]["text"]
        self.assertIn("The other Residencias Caribe building toward Avenida La Playa", e03)
        for text in (
            e03,
            "other Residencias Caribe building toward Avenida La Playa",
            "Residencias Caribe toward Avenida La Playa",
            "Caribe B",
        ):
            result = self._resolve_text(text)
            self.assertEqual(result["resolved_site_id"], W2, text)

    def test_w1_corroboration_from_e01_and_e02(self):
        e01 = self._event("E01")
        e02 = self._event("E02")
        social = make_observation(
            "obs-e01",
            timestamp=e01["raw_input"]["timestamp"],
            source_type="social",
            source_id=e01["source_id"],
            author_id=e01["raw_input"]["author_id"],
            raw_text=e01["raw_input"]["text"],
            incident_type="trapped_victims",
            victim_type="possible_multiple",
        )
        radio = make_observation(
            "obs-e02",
            timestamp=e02["raw_input"]["timestamp"],
            source_type="radio",
            source_id=e02["source_id"],
            raw_text=e02["raw_input"]["transcript"],
            incident_type="trapped_victims",
            victim_type="confirmed_multiple",
            count_min=4,
            structural_hazard="major_damage",
            metadata={"speaker_id": e02["raw_input"]["speaker_id"]},
        )
        self.assertIsNone(social["location"]["resolved_site_id"])
        self.assertIsNone(radio["location"]["resolved_site_id"])
        self.assertNotEqual(social["metadata"]["ingestion"]["source_metadata"]["author_id"], radio["source_id"])

        after_e01 = apply_observation(self.state, social, self.registry)
        self.assertEqual(after_e01.last_trace["resolved_site_id"], W1)
        self.assertEqual(after_e01.belief["state_version"], "1")
        trapped = incidents_for(after_e01, W1, "trapped_victims")
        self.assertEqual(len(trapped), 1)
        self.assertEqual(trapped[0]["verification_status"], "unverified")

        after_e02 = apply_observation(after_e01, radio, self.registry)
        self.assertEqual(after_e02.last_trace["resolved_site_id"], W1)
        self.assertEqual(after_e02.belief["state_version"], "2")
        self.assertGreater(int(after_e02.belief["state_version"]), int(after_e01.belief["state_version"]))

        trapped = incidents_for(after_e02, W1, "trapped_victims")
        self.assertEqual(len(trapped), 1)
        incident = trapped[0]
        self.assertEqual(incident["site_id"], W1)
        self.assertEqual(incident["status"], "active")
        self.assertEqual(incident["verification_status"], "corroborated")
        self.assertFalse(incident["metadata"]["evidence_conflict"])
        self.assertEqual(set(incident["observation_ids"]), {"obs-e01", "obs-e02"})
        self.assertEqual(set(incident["evidence_ids"]), {evidence_id("obs-e01"), evidence_id("obs-e02")})
        self.assertEqual(set(incident["metadata"]["supporting_observation_ids"]), {"obs-e01", "obs-e02"})

        worksite = worksite_for(after_e02, W1)
        self.assertEqual(worksite["site_id"], W1)
        self.assertEqual(worksite["active_incident_ids"], [incident["incident_id"]])
        self.assertEqual(set(worksite["evidence_ids"]), set(incident["evidence_ids"]))
        self.assertEqual(worksite["criticality"], 0)
        self.assertEqual(worksite["metadata"]["search_features"]["criticality"], 0.78)
        self.assertEqual(worksite["metadata"]["search_features"]["evidence_conflict"], 0.0)
        self.assertEqual(
            worksite["metadata"]["scenario_operational_overlay"]["source"],
            "scenario_pack.active_worksites",
        )
        self.assertTrue(worksite["metadata"]["scenario_operational_overlay"]["not_osm_criticality"])
        self.assertEqual(incidents_for(after_e02, W2), [])

    def test_w2_is_distinct_from_w1(self):
        e01 = self._event("E01")
        e03 = self._event("E03")
        w1_obs = make_observation(
            "obs-e01-w1",
            timestamp=e01["raw_input"]["timestamp"],
            source_type="social",
            source_id=e01["source_id"],
            author_id=e01["raw_input"]["author_id"],
            raw_text=e01["raw_input"]["text"],
            incident_type="trapped_victims",
            victim_type="possible_multiple",
        )
        w2_obs = make_observation(
            "obs-e03-w2",
            timestamp=e03["raw_input"]["timestamp"],
            source_type="social",
            source_id=e03["source_id"],
            author_id=e03["raw_input"]["author_id"],
            raw_text=e03["raw_input"]["text"],
            incident_type="trapped_victims",
            victim_type="possible_multiple",
        )
        state = apply_observation(self.state, w1_obs, self.registry)
        state = apply_observation(state, w2_obs, self.registry)
        self.assertEqual(state.last_trace["resolved_site_id"], W2)
        w1_incidents = incidents_for(state, W1)
        w2_incidents = incidents_for(state, W2)
        self.assertEqual(len(w1_incidents), 1)
        self.assertEqual(len(w2_incidents), 1)
        self.assertNotEqual(w1_incidents[0]["incident_id"], w2_incidents[0]["incident_id"])
        self.assertEqual(w2_incidents[0]["site_id"], W2)
        self.assertNotEqual(w2_incidents[0]["site_id"], W1)
        self.assertEqual({item["site_id"] for item in state.belief["worksites"]}, {W1, W2})
        ambiguous = apply_observation(
            state,
            make_observation(
                "obs-plain-caribe",
                timestamp="2026-09-19T14:05:00Z",
                raw_text="Residencias Caribe",
                source_id="replay",
                author_id="plain-author",
            ),
            self.registry,
        )
        self.assertEqual(ambiguous.last_trace["location_status"], "ambiguous")
        self.assertIsNone(ambiguous.last_trace["resolved_site_id"])
        self.assertEqual(set(ambiguous.last_trace["candidate_site_ids"]), {W1, W2})
        self.assertEqual(len(incidents_for(ambiguous, W1)), 1)
        self.assertEqual(len(incidents_for(ambiguous, W2)), 1)

    def test_w7_rumor_then_trusted_drone_negative(self):
        e11 = self._event("E11")
        e12 = self._event("E12")
        rumor = make_observation(
            "obs-e11",
            timestamp=e11["raw_input"]["timestamp"],
            source_type="social",
            source_id=e11["source_id"],
            author_id=e11["raw_input"]["author_id"],
            raw_text=e11["raw_input"]["text"],
            incident_type="trapped_victims",
            victim_type="possible_multiple",
        )
        drone_raw = e12["raw_input"]
        drone = make_observation(
            "obs-e12",
            timestamp=drone_raw["timestamp"],
            source_type="drone",
            source_id=e12["source_id"],
            raw_text=drone_raw["location"]["raw_text"],
            label=drone_raw["location"]["label"],
            latitude=drone_raw["location"]["latitude"],
            longitude=drone_raw["location"]["longitude"],
            incident_type="trapped_victims",
            victim_type=drone_raw["victim_indicator"]["type"],
            count_min=drone_raw["victim_indicator"]["count_min"],
            count_max=drone_raw["victim_indicator"]["count_max"],
            structural_hazard=drone_raw["structural_damage"],
            access_status=drone_raw["access_status"],
            verification_status="verified",
        )
        self.assertEqual(drone["victim_indication"]["type"], "none")
        self.assertIsNone(drone["location"]["resolved_site_id"])
        self.assertIsNone(drone_raw["location"]["resolved_site_id"])

        after_rumor = apply_observation(self.state, rumor, self.registry)
        self.assertEqual(after_rumor.last_trace["resolved_site_id"], W7)
        incident = incidents_for(after_rumor, W7, "trapped_victims")[0]
        self.assertEqual(incident["status"], "active")
        self.assertIn(incident["verification_status"], {"unverified", "potential"})
        worksite = worksite_for(after_rumor, W7)
        self.assertEqual(worksite["operational_status"], "pending")
        self.assertEqual(worksite["active_incident_ids"], [incident["incident_id"]])
        self.assertEqual(worksite["criticality"], 0)
        self.assertEqual(worksite["metadata"]["search_features"]["criticality"], 0.88)

        after_drone = apply_observation(after_rumor, drone, self.registry)
        self.assertEqual(after_drone.last_trace["resolved_site_id"], W7)
        updated = incidents_for(after_drone, W7, "trapped_victims")[0]
        self.assertEqual(updated["incident_id"], incident["incident_id"])
        self.assertEqual(updated["status"], "invalidated")
        self.assertNotEqual(updated["verification_status"], "corroborated")
        self.assertTrue(updated["metadata"]["evidence_conflict"])
        self.assertIn("obs-e11", updated["metadata"]["supporting_observation_ids"])
        self.assertIn("obs-e12", updated["metadata"]["conflicting_observation_ids"])
        self.assertEqual(set(updated["observation_ids"]), {"obs-e11", "obs-e12"})
        self.assertEqual(set(updated["evidence_ids"]), {evidence_id("obs-e11"), evidence_id("obs-e12")})

        worksite = worksite_for(after_drone, W7)
        self.assertEqual(worksite["site_id"], W7)
        self.assertEqual(worksite["operational_status"], "resolved")
        self.assertEqual(worksite["active_incident_ids"], [])
        self.assertEqual(worksite["metadata"]["invalidated_incident_ids"], [updated["incident_id"]])
        self.assertEqual(set(worksite["evidence_ids"]), set(updated["evidence_ids"]))
        self.assertEqual(worksite["metadata"]["search_features"]["evidence_conflict"], 0.0)

    def test_w4_unknown_occupants_are_not_none(self):
        e06 = self._event("E06")
        text = e06["raw_input"]["text"]
        self.assertIn("Parque Caraballeda", text)
        self.assertIn("No tenemos información sobre los ocupantes", text)
        observation = make_observation(
            "obs-e06",
            timestamp=e06["raw_input"]["timestamp"],
            source_type="social",
            source_id=e06["source_id"],
            author_id=e06["raw_input"]["author_id"],
            raw_text=text,
            incident_type="structural_damage",
            victim_type="unknown",
            structural_hazard="severe_collapse",
        )
        state = apply_observation(self.state, observation, self.registry)
        self.assertEqual(state.last_trace["resolved_site_id"], W4)
        structural = incidents_for(state, W4, "structural_damage")
        trapped = incidents_for(state, W4, "trapped_victims")
        self.assertEqual(len(structural), 1)
        self.assertEqual(trapped, [])
        incident = structural[0]
        self.assertEqual(incident["status"], "active")
        self.assertEqual(incident["structural_state"]["severity"], "severe")
        victim = (incident.get("victim_state") or {}).get("indication") or {}
        self.assertNotEqual(victim.get("type"), "none")
        worksite = worksite_for(state, W4)
        self.assertEqual(worksite["structural_summary"]["severity"], "severe")
        self.assertEqual(worksite["victim_summary"]["indication"]["type"], "unknown")
        self.assertNotEqual(worksite["victim_summary"]["indication"]["type"], "none")

    def test_w6_low_evidence_does_not_manufacture_trapped_victims(self):
        e13 = self._event("E13")
        text = e13["raw_input"]["text"]
        self.assertIn("Residencias Las Perlas", text)
        self.assertIn("No word on occupants", text)
        observation = make_observation(
            "obs-e13",
            timestamp=e13["raw_input"]["timestamp"],
            source_type="social",
            source_id=e13["source_id"],
            author_id=e13["raw_input"]["author_id"],
            raw_text=text,
            incident_type="structural_damage",
            victim_type="unknown",
            structural_hazard="reported_damage",
        )
        state = apply_observation(self.state, observation, self.registry)
        self.assertEqual(state.last_trace["resolved_site_id"], W6)
        self.assertEqual(incidents_for(state, W6, "trapped_victims"), [])
        structural = incidents_for(state, W6, "structural_damage")
        self.assertEqual(len(structural), 1)
        self.assertEqual(structural[0]["structural_state"]["severity"], "minor")
        worksite = worksite_for(state, W6)
        self.assertEqual(worksite["structural_summary"]["severity"], "minor")
        self.assertEqual(worksite["victim_summary"]["indication"]["type"], "unknown")
        self.assertEqual(worksite["metadata"]["search_features"]["criticality"], 0.32)


if __name__ == "__main__":
    unittest.main()
