"""OperationalBeliefState reducer, versioning, idempotency, and end-to-end fusion."""

import json
import tempfile
import unittest
from pathlib import Path

from operational.belief_state import apply_observation, empty_state, retry_unresolved
from operational.ids import worksite_id
from operational.site_registry import InMemorySiteRegistry, ScenarioPackSiteRegistry
from operational.validation import validate_belief_state
from tests.operational_fixtures import T0, make_observation, make_site, test_registry


class BeliefStateTests(unittest.TestCase):
    def setUp(self):
        self.registry = test_registry()
        self.state = empty_state(T0)

    def test_empty_state_validates(self):
        belief = self.state.to_belief_state()
        self.assertEqual(belief["state_version"], "0")
        self.assertEqual(belief["incidents"], [])
        self.assertEqual(belief["worksites"], [])
        self.assertEqual(belief["resources"], [])
        self.assertEqual(belief["movement_states"], [])
        self.assertEqual(belief["active_routes"], [])

    def test_empty_to_one_worksite(self):
        observation = make_observation(
            "obs-social-riverside-001",
            timestamp=T0,
            source_type="social",
            source_id="community-post-1",
            raw_text="Riverside Apartments",
            incident_type="trapped_victims",
            victim_type="possible_multiple",
        )
        next_state = apply_observation(self.state, observation, self.registry)
        belief = next_state.to_belief_state()
        self.assertEqual(belief["state_version"], "1")
        self.assertEqual(len(belief["worksites"]), 1)
        self.assertEqual(belief["worksites"][0]["site_id"], "site-s1")
        self.assertEqual(len(belief["incidents"]), 1)
        self.assertEqual(belief["incidents"][0]["incident_type"], "trapped_victims")
        self.assertTrue(next_state.last_trace["changed"])

    def test_duplicate_observation_is_noop(self):
        observation = make_observation(
            "obs-dup",
            raw_text="Riverside Apartments",
        )
        once = apply_observation(self.state, observation, self.registry)
        twice = apply_observation(once, observation, self.registry)
        self.assertEqual(twice.state_version, once.state_version)
        self.assertFalse(twice.last_trace["changed"])
        self.assertEqual(twice.last_trace["reason"], "duplicate_observation")
        self.assertEqual(len(twice.belief["incidents"][0]["observation_ids"]), 1)

    def test_unresolved_observation_is_preserved_and_does_not_create_incident(self):
        observation = make_observation("obs-unknown", raw_text="mystery plaza")
        next_state = apply_observation(self.state, observation, self.registry)
        self.assertEqual(next_state.state_version, "1")
        self.assertEqual(next_state.belief["incidents"], [])
        self.assertEqual(next_state.belief["worksites"], [])
        self.assertEqual(len(next_state.unresolved), 1)
        self.assertEqual(next_state.unresolved[0]["location_resolution"]["resolution_status"], "unresolved")
        validate_belief_state(next_state.belief)

        again = apply_observation(next_state, observation, self.registry)
        self.assertEqual(again.state_version, "1")
        self.assertEqual(len(again.unresolved), 1)

    def test_unresolved_can_be_reresolved_after_registry_update(self):
        observation = make_observation("obs-new-alias", raw_text="New Shelter")
        pending = apply_observation(self.state, observation, self.registry)
        self.assertEqual(pending.belief["incidents"], [])

        updated = InMemorySiteRegistry.from_sites(
            self.registry.list_sites() + [
                make_site("site-shelter", "New Shelter", 10.63, -66.86, aliases=["New Shelter"])
            ],
            {
                **{"site-s1": ["Riverside Apartments"], "site-shelter": ["New Shelter"]},
            },
        )
        resolved = retry_unresolved(pending, updated)
        self.assertEqual(resolved.last_trace["incident_action"], "created")
        self.assertEqual(resolved.belief["incidents"][0]["site_id"], "site-shelter")
        self.assertEqual(resolved.unresolved, [])
        self.assertNotEqual(resolved.state_version, pending.state_version)

    def test_retry_processing_time_does_not_extend_fusion_window(self):
        early = make_observation(
            "obs-unresolved-early",
            timestamp=T0,
            raw_text="New Shelter",
            source_id="replay",
            author_id="early-author",
        )
        pending = apply_observation(self.state, early, self.registry)
        later_clock = apply_observation(
            pending,
            make_observation(
                "obs-riverside-clock",
                timestamp="2026-09-19T16:00:00Z",
                raw_text="Riverside Apartments",
            ),
            self.registry,
            scenario_time="2026-09-19T16:00:00Z",
        )
        self.assertEqual(later_clock.belief["scenario_time"], "2026-09-19T16:00:00Z")

        expanded = InMemorySiteRegistry.from_sites(
            self.registry.list_sites() + [
                make_site("site-shelter", "New Shelter", 10.63, -66.86, aliases=["New Shelter"])
            ],
            {"site-s1": ["Riverside Apartments"], "site-shelter": ["New Shelter"]},
        )
        resolved = retry_unresolved(later_clock, expanded)
        shelter = next(item for item in resolved.belief["incidents"] if item["site_id"] == "site-shelter")
        self.assertEqual(shelter["first_observed_at"], T0)
        self.assertEqual(shelter["metadata"]["latest_evidence_at"], T0)

        unrelated = make_observation(
            "obs-shelter-two-hours-later",
            timestamp="2026-09-19T16:00:00Z",
            raw_text="New Shelter",
            source_id="replay",
            author_id="later-author",
        )
        after = apply_observation(resolved, unrelated, expanded, scenario_time="2026-09-19T16:00:00Z")
        shelter_incidents = [item for item in after.belief["incidents"] if item["site_id"] == "site-shelter"]
        self.assertEqual(len(shelter_incidents), 2)
        ids = {item["incident_id"] for item in shelter_incidents}
        self.assertEqual(len(ids), 2)

    def test_ambiguous_preserves_candidates_and_creates_no_incident(self):
        observation = make_observation(
            "obs-costa",
            raw_text="Costa Brava",
            incident_type="structural_damage",
            victim_type="unknown",
            structural_hazard="major_damage",
        )
        next_state = apply_observation(self.state, observation, self.registry)
        resolution = next_state.last_trace
        self.assertEqual(resolution["location_status"], "ambiguous")
        self.assertEqual(set(resolution["candidate_site_ids"]), {"site-costa-a", "site-costa-b"})
        self.assertEqual(next_state.belief["incidents"], [])
        self.assertEqual(next_state.unresolved[0]["location_resolution"]["candidate_site_ids"],
                         sorted(["site-costa-a", "site-costa-b"]))

    def test_pass_through_collections_remain_empty(self):
        observation = make_observation("obs-1", raw_text="Riverside Apartments")
        next_state = apply_observation(self.state, observation, self.registry)
        belief = next_state.to_belief_state()
        for key in ("resources", "road_states", "movement_states", "active_routes", "pending_reassignment_proposals"):
            self.assertEqual(belief[key], [])

    def test_deterministic_output_order(self):
        first = make_observation(
            "obs-b",
            raw_text="Hill Clinic",
            incident_type="access_blocked",
            victim_type="unknown",
            access_status="blocked",
        )
        second = make_observation(
            "obs-a",
            timestamp="2026-09-19T14:01:00Z",
            raw_text="Riverside Apartments",
        )
        state = apply_observation(self.state, first, self.registry)
        state = apply_observation(state, second, self.registry)
        ids = [item["incident_id"] for item in state.belief["incidents"]]
        self.assertEqual(ids, sorted(ids))
        site_ids = [item["site_id"] for item in state.belief["sites"]]
        self.assertEqual(site_ids, sorted(site_ids))

    def test_scenario_pack_registry_adapter_reads_generic_json(self):
        document = {
            "sites": [
                {
                    **make_site("site-pack-1", "Pack Site", 10.5, -66.8, aliases=["Pack Site"]),
                    "aliases": ["Pack Site", "the pack site"],
                }
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "active_sites.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            registry = ScenarioPackSiteRegistry(path)
        observation = make_observation("obs-pack", raw_text="the pack site")
        state = apply_observation(empty_state(T0), observation, registry)
        self.assertEqual(state.belief["incidents"][0]["site_id"], "site-pack-1")

    def test_end_to_end_riverside_then_costa_brava(self):
        social = make_observation(
            "obs-social-riverside-001",
            timestamp="2026-09-19T14:00:00Z",
            source_type="social",
            source_id="community-post-1",
            raw_text="Riverside Apartments",
            label="Riverside Apartments",
            accuracy="building",
            incident_type="trapped_victims",
            victim_type="possible_multiple",
            count_min=None,
            count_max=None,
        )
        state = apply_observation(self.state, social, self.registry)
        belief = state.to_belief_state()
        self.assertEqual(belief["state_version"], "1")
        self.assertEqual(len(belief["sites"]), 1)
        self.assertEqual(belief["sites"][0]["site_id"], "site-s1")
        self.assertEqual(len(belief["incidents"]), 1)
        incident = belief["incidents"][0]
        self.assertEqual(incident["incident_type"], "trapped_victims")
        self.assertEqual(incident["site_id"], "site-s1")
        self.assertEqual(incident["observation_ids"], ["obs-social-riverside-001"])
        self.assertEqual(incident["verification_status"], "unverified")
        self.assertEqual(len(belief["worksites"]), 1)
        worksite = belief["worksites"][0]
        self.assertEqual(worksite["worksite_id"], worksite_id("site-s1"))
        self.assertEqual(worksite["active_incident_ids"], [incident["incident_id"]])
        self.assertEqual(worksite["evidence_status"], "unverified")
        self.assertEqual(state.last_trace["incident_action"], "created")
        trapped_id = incident["incident_id"]

        radio = make_observation(
            "obs-radio-riverside-002",
            timestamp="2026-09-19T14:08:00Z",
            source_type="radio",
            source_id="rescue-alpha",
            raw_text="Riverside Apartments",
            incident_type="trapped_victims",
            victim_type="confirmed_multiple",
            count_min=4,
            count_max=None,
        )
        state = apply_observation(state, radio, self.registry)
        belief = state.to_belief_state()
        self.assertEqual(belief["state_version"], "2")
        self.assertEqual(len(belief["incidents"]), 1)
        incident = belief["incidents"][0]
        self.assertEqual(incident["incident_id"], trapped_id)
        self.assertEqual(incident["observation_ids"], [
            "obs-social-riverside-001",
            "obs-radio-riverside-002",
        ])
        self.assertEqual(len(incident["evidence_ids"]), 2)
        self.assertEqual(incident["victim_state"]["indication"]["type"], "confirmed_multiple")
        self.assertEqual(incident["victim_state"]["count_min"], 4)
        self.assertEqual(incident["victim_state"]["best_estimate"], 4)
        self.assertEqual(incident["verification_status"], "corroborated")
        self.assertEqual(belief["worksites"][0]["evidence_status"], "corroborated")
        self.assertEqual(state.last_trace["incident_action"], "updated")
        self.assertEqual(len(belief["worksites"]), 1)

        blocked = make_observation(
            "obs-social-riverside-003",
            timestamp="2026-09-19T14:12:00Z",
            source_type="social",
            source_id="community-post-2",
            raw_text="Riverside Apartments",
            incident_type="access_blocked",
            victim_type="unknown",
            access_status="blocked",
        )
        state = apply_observation(state, blocked, self.registry)
        belief = state.to_belief_state()
        self.assertEqual(belief["state_version"], "3")
        types = {item["incident_type"] for item in belief["incidents"]}
        self.assertEqual(types, {"trapped_victims", "access_blocked"})
        self.assertEqual(len(belief["incidents"]), 2)
        worksite = belief["worksites"][0]
        self.assertEqual(len(worksite["active_incident_ids"]), 2)
        self.assertEqual(worksite["access_summary"]["status"], "blocked")
        self.assertEqual(worksite["victim_summary"]["indication"]["type"], "confirmed_multiple")

        costa = make_observation(
            "obs-social-costa-004",
            timestamp="2026-09-19T14:15:00Z",
            source_type="social",
            source_id="community-post-3",
            raw_text="Costa Brava",
            incident_type="structural_damage",
            victim_type="unknown",
            structural_hazard="major_damage",
        )
        state = apply_observation(state, costa, self.registry)
        belief = state.to_belief_state()
        self.assertEqual(belief["state_version"], "4")
        self.assertEqual(state.last_trace["location_status"], "ambiguous")
        self.assertEqual(set(state.last_trace["candidate_site_ids"]), {"site-costa-a", "site-costa-b"})
        self.assertIsNone(state.last_trace["incident_id"])
        self.assertEqual(len(belief["incidents"]), 2)
        self.assertEqual(len(belief["worksites"]), 1)
        self.assertEqual(len(state.unresolved), 1)
        self.assertEqual(
            state.unresolved[0]["location_resolution"]["resolution_status"],
            "ambiguous",
        )

        duplicate = apply_observation(state, radio, self.registry)
        self.assertEqual(duplicate.state_version, "4")
        self.assertEqual(len(duplicate.belief["incidents"]), 2)


if __name__ == "__main__":
    unittest.main()
