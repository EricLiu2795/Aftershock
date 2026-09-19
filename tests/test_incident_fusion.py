"""IncidentFusion: same site/type merges; evidence and uncertainty are preserved."""

from datetime import timedelta
import unittest

from operational.incident_fusion import (
    DEFAULT_FUSION_WINDOW,
    FusionConfig,
    IncidentFusion,
    aggregate_verification,
    aggregate_victim_state,
)
from operational.ids import incident_id
from operational.location_resolver import LocationResolver
from operational.validation import validate_incident
from tests.operational_fixtures import T0, make_observation, test_registry


class IncidentFusionTests(unittest.TestCase):
    def setUp(self):
        self.registry = test_registry()
        self.resolver = LocationResolver()
        self.fusion = IncidentFusion()

    def _resolved(self, observation):
        return observation, self.resolver.resolve(observation, self.registry)

    def test_same_site_same_type_merges(self):
        first = make_observation(
            "obs-a",
            raw_text="Riverside Apartments",
            incident_type="structural_damage",
            victim_type="unknown",
            structural_hazard="major_damage",
        )
        second = make_observation(
            "obs-b",
            timestamp="2026-09-19T14:10:00Z",
            source_type="radio",
            source_id="rescue-alpha",
            raw_text="Riverside Apartments",
            incident_type="structural_damage",
            victim_type="unknown",
            structural_hazard="severe_collapse",
        )
        obs, resolution = self._resolved(first)
        created = self.fusion.fuse(obs, resolution, [])
        self.assertEqual(created.action, "created")
        updated = self.fusion.fuse(second, resolution, [created.incident])
        self.assertEqual(updated.action, "updated")
        self.assertEqual(updated.incident["incident_id"], created.incident["incident_id"])
        self.assertEqual(updated.incident["observation_ids"], ["obs-a", "obs-b"])
        self.assertEqual(len(updated.incident["evidence_ids"]), 2)
        validate_incident(updated.incident)

    def test_same_site_different_type_stays_separate(self):
        structural = make_observation(
            "obs-struct",
            raw_text="Riverside Apartments",
            incident_type="structural_damage",
            victim_type="unknown",
            structural_hazard="major_damage",
        )
        trapped = make_observation(
            "obs-trap",
            timestamp="2026-09-19T14:05:00Z",
            raw_text="Riverside Apartments",
            incident_type="trapped_victims",
            victim_type="possible_multiple",
        )
        _, resolution = self._resolved(structural)
        first = self.fusion.fuse(structural, resolution, [])
        second = self.fusion.fuse(trapped, resolution, [first.incident])
        self.assertEqual(second.action, "created")
        self.assertNotEqual(second.incident["incident_id"], first.incident["incident_id"])
        self.assertEqual(second.incident["incident_type"], "trapped_victims")
        self.assertEqual(first.incident["incident_type"], "structural_damage")

    def test_different_site_never_merges(self):
        first = make_observation(
            "obs-r",
            raw_text="Riverside Apartments",
            incident_type="trapped_victims",
        )
        second = make_observation(
            "obs-h",
            raw_text="Hill Clinic",
            incident_type="trapped_victims",
            timestamp="2026-09-19T14:05:00Z",
        )
        a, res_a = self._resolved(first)
        b, res_b = self._resolved(second)
        created = self.fusion.fuse(a, res_a, [])
        other = self.fusion.fuse(b, res_b, [created.incident])
        self.assertEqual(other.action, "created")
        self.assertNotEqual(other.incident["site_id"], created.incident["site_id"])
        self.assertNotEqual(other.incident["incident_id"], created.incident["incident_id"])

    def test_fusion_time_window_creates_new_incident(self):
        self.assertEqual(DEFAULT_FUSION_WINDOW, timedelta(minutes=30))
        first = make_observation(
            "obs-early",
            raw_text="Riverside Apartments",
            timestamp=T0,
        )
        later = make_observation(
            "obs-late",
            raw_text="Riverside Apartments",
            timestamp="2026-09-19T14:31:00Z",
            source_id="src-2",
        )
        obs, resolution = self._resolved(first)
        created = self.fusion.fuse(obs, resolution, [])
        result = self.fusion.fuse(later, resolution, [created.incident])
        self.assertEqual(result.action, "created")
        self.assertNotEqual(result.incident["incident_id"], created.incident["incident_id"])

        inside = make_observation(
            "obs-inside",
            raw_text="Riverside Apartments",
            timestamp="2026-09-19T14:30:00Z",
            source_id="src-3",
        )
        merged = self.fusion.fuse(inside, resolution, [created.incident])
        self.assertEqual(merged.action, "updated")

    def test_configurable_window_is_not_a_hidden_magic_number(self):
        tight = IncidentFusion(FusionConfig(window=timedelta(minutes=5)))
        first = make_observation("obs-1", raw_text="Riverside Apartments", timestamp=T0)
        second = make_observation(
            "obs-2",
            raw_text="Riverside Apartments",
            timestamp="2026-09-19T14:06:00Z",
        )
        _, resolution = self._resolved(first)
        created = tight.fuse(first, resolution, [])
        result = tight.fuse(second, resolution, [created.incident])
        self.assertEqual(result.action, "created")

    def test_unresolved_location_does_not_create_incident(self):
        observation = make_observation("obs-u", raw_text="nowhere")
        resolution = self.resolver.resolve(observation, self.registry)
        result = self.fusion.fuse(observation, resolution, [])
        self.assertEqual(result.action, "none")
        self.assertIsNone(result.incident)

    def test_evidence_preserved_and_duplicate_observation_skipped(self):
        first = make_observation("obs-dup", raw_text="Riverside Apartments")
        _, resolution = self._resolved(first)
        created = self.fusion.fuse(first, resolution, [])
        again = self.fusion.fuse(first, resolution, [created.incident])
        self.assertEqual(again.action, "none")
        self.assertEqual(created.incident["observation_ids"], ["obs-dup"])
        self.assertEqual(len(created.incident["evidence_ids"]), 1)

    def test_victim_unknown_vs_none(self):
        unknown = {
            "observation_id": "obs-unknown",
            "source_type": "social",
            "source_key": "a",
            "verification_status": "unverified",
            "timestamp": T0,
            "victim_type": "unknown",
            "count_min": None,
            "count_max": None,
        }
        none = dict(unknown, observation_id="obs-none", victim_type="none", source_key="b")
        confirmed = dict(
            unknown,
            observation_id="obs-conf",
            source_type="radio",
            source_key="c",
            victim_type="confirmed_multiple",
            count_min=4,
            count_max=None,
        )
        fused = aggregate_victim_state([unknown, confirmed])
        self.assertEqual(fused["indication"]["type"], "confirmed_multiple")
        self.assertEqual(fused["best_estimate"], 4)
        self.assertEqual(fused["count_min"], 4)
        self.assertNotEqual(fused["indication"]["type"], "none")
        self.assertNotIn("obs-unknown", fused["supporting_observation_ids"])

        none_only = aggregate_victim_state([none])
        self.assertEqual(none_only["indication"]["type"], "none")

        access_none_does_not_apply = aggregate_victim_state([confirmed])
        self.assertEqual(access_none_does_not_apply["indication"]["type"], "confirmed_multiple")

    def test_possible_unknown_count_plus_confirmed_count(self):
        social = make_observation(
            "obs-social",
            raw_text="Riverside Apartments",
            victim_type="possible_multiple",
            count_min=None,
            count_max=None,
        )
        radio = make_observation(
            "obs-radio",
            timestamp="2026-09-19T14:08:00Z",
            source_type="radio",
            source_id="rescue-alpha",
            raw_text="Riverside Apartments",
            victim_type="confirmed_multiple",
            count_min=4,
        )
        _, resolution = self._resolved(social)
        created = self.fusion.fuse(social, resolution, [])
        updated = self.fusion.fuse(radio, resolution, [created.incident])
        victim = updated.incident["victim_state"]
        self.assertEqual(victim["indication"]["type"], "confirmed_multiple")
        self.assertEqual(victim["best_estimate"], 4)
        self.assertEqual(victim["count_min"], 4)
        self.assertEqual(set(updated.incident["observation_ids"]), {"obs-social", "obs-radio"})

    def test_count_envelope_does_not_treat_max_as_best_estimate(self):
        social = make_observation(
            "obs-social-10",
            raw_text="Riverside Apartments",
            victim_type="possible_multiple",
            count_min=10,
            count_max=10,
        )
        radio = make_observation(
            "obs-radio-3",
            timestamp="2026-09-19T14:08:00Z",
            source_type="radio",
            source_id="rescue-alpha",
            raw_text="Riverside Apartments",
            victim_type="confirmed_multiple",
            count_min=3,
            count_max=3,
            verification_status="unverified",
        )
        _, resolution = self._resolved(social)
        created = self.fusion.fuse(social, resolution, [])
        updated = self.fusion.fuse(radio, resolution, [created.incident])
        victim = updated.incident["victim_state"]
        self.assertEqual(victim["best_estimate"], 3)
        self.assertEqual(victim["count_min"], 3)
        self.assertEqual(victim["count_max"], 10)

    def test_unverified_does_not_become_verified_from_one_social_report(self):
        social = make_observation("obs-s", raw_text="Riverside Apartments")
        _, resolution = self._resolved(social)
        created = self.fusion.fuse(social, resolution, [])
        self.assertEqual(created.incident["verification_status"], "unverified")

        radio = make_observation(
            "obs-r",
            timestamp="2026-09-19T14:04:00Z",
            source_type="radio",
            source_id="rescue-alpha",
            raw_text="Riverside Apartments",
            victim_type="confirmed_multiple",
            count_min=4,
        )
        updated = self.fusion.fuse(radio, resolution, [created.incident])
        self.assertEqual(updated.incident["verification_status"], "corroborated")

        drone = make_observation(
            "obs-d",
            timestamp="2026-09-19T14:06:00Z",
            source_type="drone",
            source_id="drone-2",
            raw_text="Riverside Apartments",
            resolved_site_id="site-s1",
            victim_type="confirmed_multiple",
            count_min=4,
            verification_status="verified",
        )
        drone_resolution = self.resolver.resolve(drone, self.registry)
        verified = self.fusion.fuse(drone, drone_resolution, [updated.incident])
        self.assertEqual(verified.incident["verification_status"], "verified")

    def test_single_verified_social_report_is_not_trusted_verified(self):
        social = make_observation(
            "obs-s-verified-claim",
            raw_text="Riverside Apartments",
            verification_status="verified",
        )
        _, resolution = self._resolved(social)
        created = self.fusion.fuse(social, resolution, [])
        self.assertNotEqual(created.incident["verification_status"], "verified")
        self.assertEqual(created.incident["verification_status"], "unverified")

    def test_incident_ids_are_deterministic(self):
        first = make_observation("obs-id", raw_text="Riverside Apartments")
        _, resolution = self._resolved(first)
        a = self.fusion.fuse(first, resolution, [])
        b = self.fusion.fuse(first, resolution, [])
        self.assertEqual(a.incident["incident_id"], b.incident["incident_id"])
        self.assertEqual(
            a.incident["incident_id"],
            incident_id("site-s1", "trapped_victims", "obs-id"),
        )
        self.assertNotIn("uuid", a.incident["incident_id"])

    def test_access_observation_does_not_downgrade_victim_incident(self):
        trapped = make_observation(
            "obs-trap",
            raw_text="Riverside Apartments",
            incident_type="trapped_victims",
            victim_type="possible_multiple",
        )
        access = make_observation(
            "obs-access",
            timestamp="2026-09-19T14:05:00Z",
            raw_text="Riverside Apartments",
            incident_type="access_blocked",
            victim_type="none",
            access_status="blocked",
        )
        _, resolution = self._resolved(trapped)
        created = self.fusion.fuse(trapped, resolution, [])
        other = self.fusion.fuse(access, resolution, [created.incident])
        self.assertEqual(other.action, "created")
        self.assertEqual(created.incident["victim_state"]["indication"]["type"], "possible_multiple")
        self.assertEqual(other.incident["incident_type"], "access_blocked")

    def test_independent_source_aggregation_helper(self):
        contributions = [
            {
                "source_type": "social",
                "source_key": "p1",
                "verification_status": "unverified",
            },
            {
                "source_type": "social",
                "source_key": "p1",
                "verification_status": "unverified",
            },
        ]
        self.assertEqual(aggregate_verification(contributions), "unverified")
        contributions.append({
            "source_type": "radio",
            "source_key": "unit-1",
            "verification_status": "unverified",
        })
        self.assertEqual(aggregate_verification(contributions), "corroborated")


if __name__ == "__main__":
    unittest.main()
