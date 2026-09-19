"""IncidentFusion: same site/type merges; evidence and uncertainty are preserved."""

from datetime import timedelta
import unittest

from operational.incident_fusion import (
    DEFAULT_FUSION_WINDOW,
    FusionConfig,
    IncidentFusion,
    aggregate_verification,
    aggregate_victim_state,
    independent_source_key,
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
                "observation_id": "obs-a",
                "source_type": "social",
                "source_key": "social|author|p1",
                "source_identity": ["social", "author", "p1"],
                "verification_status": "unverified",
                "victim_type": "possible_multiple",
                "timestamp": T0,
            },
            {
                "observation_id": "obs-b",
                "source_type": "social",
                "source_key": "social|author|p1",
                "source_identity": ["social", "author", "p1"],
                "verification_status": "unverified",
                "victim_type": "possible_multiple",
                "timestamp": T0,
            },
        ]
        self.assertEqual(aggregate_verification(contributions, "trapped_victims"), "unverified")
        contributions.append({
            "observation_id": "obs-c",
            "source_type": "radio",
            "source_key": "radio|unit|unit-1",
            "source_identity": ["radio", "unit", "unit-1"],
            "verification_status": "unverified",
            "victim_type": "confirmed_individual",
            "timestamp": T0,
        })
        self.assertEqual(aggregate_verification(contributions, "trapped_victims"), "corroborated")

    def test_corroboration_requires_agreement(self):
        social = make_observation(
            "obs-social-trap",
            raw_text="Riverside Apartments",
            source_id="replay",
            author_id="author-a",
            victim_type="possible_multiple",
        )
        radio = make_observation(
            "obs-radio-voices",
            timestamp="2026-09-19T14:04:00Z",
            source_type="radio",
            source_id="rescue-alpha",
            raw_text="Riverside Apartments",
            victim_type="confirmed_multiple",
            count_min=2,
        )
        _, resolution = self._resolved(social)
        created = self.fusion.fuse(social, resolution, [])
        updated = self.fusion.fuse(radio, resolution, [created.incident])
        self.assertEqual(updated.incident["verification_status"], "corroborated")
        self.assertFalse(updated.incident["metadata"]["evidence_conflict"])
        self.assertEqual(set(updated.incident["metadata"]["supporting_observation_ids"]), {
            "obs-social-trap", "obs-radio-voices",
        })

        collapse = make_observation(
            "obs-social-school",
            raw_text="Riverside Apartments",
            incident_type="structural_damage",
            victim_type="unknown",
            structural_hazard="severe_collapse",
            source_id="replay",
            author_id="author-b",
        )
        intact = make_observation(
            "obs-drone-intact",
            timestamp="2026-09-19T14:06:00Z",
            source_type="drone",
            source_id="drone-2",
            raw_text="Riverside Apartments",
            resolved_site_id="site-s1",
            incident_type="structural_damage",
            victim_type="unknown",
            structural_hazard="none",
            verification_status="unverified",
        )
        _, structural_resolution = self._resolved(collapse)
        rumor = self.fusion.fuse(collapse, structural_resolution, [])
        contradicted = self.fusion.fuse(intact, self.resolver.resolve(intact, self.registry), [rumor.incident])
        self.assertEqual(contradicted.incident["verification_status"], "unverified")
        self.assertTrue(contradicted.incident["metadata"]["evidence_conflict"])
        self.assertEqual(contradicted.incident["metadata"]["supporting_observation_ids"], ["obs-social-school"])
        self.assertEqual(contradicted.incident["metadata"]["conflicting_observation_ids"], ["obs-drone-intact"])
        self.assertNotEqual(contradicted.incident["verification_status"], "corroborated")

    def test_access_agreement_and_conflict(self):
        blocked = make_observation(
            "obs-social-blocked",
            raw_text="Riverside Apartments",
            incident_type="access_blocked",
            victim_type="unknown",
            access_status="blocked",
            source_id="replay",
            author_id="author-c",
        )
        confirm = make_observation(
            "obs-radio-blocked",
            timestamp="2026-09-19T14:05:00Z",
            source_type="radio",
            source_id="rescue-bravo",
            raw_text="Riverside Apartments",
            incident_type="access_blocked",
            victim_type="unknown",
            access_status="blocked",
        )
        _, resolution = self._resolved(blocked)
        created = self.fusion.fuse(blocked, resolution, [])
        agreed = self.fusion.fuse(confirm, resolution, [created.incident])
        self.assertEqual(agreed.incident["verification_status"], "corroborated")
        self.assertFalse(agreed.incident["metadata"]["evidence_conflict"])

        open_road = make_observation(
            "obs-drone-open",
            timestamp="2026-09-19T14:06:00Z",
            source_type="drone",
            source_id="drone-1",
            raw_text="Riverside Apartments",
            resolved_site_id="site-s1",
            incident_type="access_blocked",
            victim_type="unknown",
            access_status="open",
        )
        conflicted = self.fusion.fuse(
            open_road,
            self.resolver.resolve(open_road, self.registry),
            [created.incident],
        )
        self.assertNotEqual(conflicted.incident["verification_status"], "corroborated")
        self.assertTrue(conflicted.incident["metadata"]["evidence_conflict"])
        self.assertEqual(conflicted.incident["access_state"]["status"], "open")

    def test_w7_trusted_drone_invalidates_unverified_rumor(self):
        rumor = make_observation(
            "obs-w7-social",
            raw_text="Riverside Apartments",
            source_id="replay",
            author_id="rumor-author",
            incident_type="trapped_victims",
            victim_type="possible_multiple",
        )
        drone = make_observation(
            "obs-w7-drone",
            timestamp="2026-09-19T14:10:00Z",
            source_type="drone",
            source_id="drone-2",
            raw_text="Riverside Apartments",
            resolved_site_id="site-s1",
            incident_type="trapped_victims",
            victim_type="none",
            count_min=0,
            count_max=0,
            structural_hazard="none",
            access_status="open",
            verification_status="verified",
        )
        _, resolution = self._resolved(rumor)
        created = self.fusion.fuse(rumor, resolution, [])
        self.assertEqual(created.incident["status"], "active")
        self.assertEqual(created.incident["verification_status"], "unverified")
        updated = self.fusion.fuse(drone, self.resolver.resolve(drone, self.registry), [created.incident])
        self.assertEqual(updated.incident["status"], "invalidated")
        self.assertNotEqual(updated.incident["verification_status"], "corroborated")
        self.assertEqual(updated.incident["observation_ids"], ["obs-w7-social", "obs-w7-drone"])
        self.assertEqual(len(updated.incident["evidence_ids"]), 2)
        self.assertTrue(updated.incident["metadata"]["evidence_conflict"])
        self.assertEqual(updated.incident["metadata"]["conflicting_observation_ids"], ["obs-w7-drone"])

    def test_trusted_negative_invalidates_structural_and_access_rumors(self):
        collapse = make_observation(
            "obs-struct-rumor",
            raw_text="Riverside Apartments",
            incident_type="structural_damage",
            victim_type="unknown",
            structural_hazard="major_damage",
        )
        intact = make_observation(
            "obs-struct-drone",
            timestamp="2026-09-19T14:07:00Z",
            source_type="drone",
            source_id="drone-2",
            raw_text="Riverside Apartments",
            resolved_site_id="site-s1",
            incident_type="structural_damage",
            victim_type="unknown",
            structural_hazard="none",
            verification_status="verified",
        )
        _, resolution = self._resolved(collapse)
        created = self.fusion.fuse(collapse, resolution, [])
        updated = self.fusion.fuse(intact, self.resolver.resolve(intact, self.registry), [created.incident])
        self.assertEqual(updated.incident["status"], "invalidated")
        self.assertNotEqual(updated.incident["verification_status"], "corroborated")

        blocked = make_observation(
            "obs-access-rumor",
            raw_text="Hill Clinic",
            incident_type="access_blocked",
            victim_type="unknown",
            access_status="blocked",
        )
        opened = make_observation(
            "obs-access-field",
            timestamp="2026-09-19T14:07:00Z",
            source_type="field_note",
            source_id="field-1",
            raw_text="Hill Clinic",
            incident_type="access_blocked",
            victim_type="unknown",
            access_status="open",
            verification_status="verified",
        )
        _, hill = self._resolved(blocked)
        access = self.fusion.fuse(blocked, hill, [])
        cleared = self.fusion.fuse(opened, hill, [access.incident])
        self.assertEqual(cleared.incident["status"], "invalidated")

    def test_unverified_negative_social_does_not_invalidate_stronger_evidence(self):
        radio = make_observation(
            "obs-radio-confirmed",
            source_type="radio",
            source_id="rescue-alpha",
            raw_text="Riverside Apartments",
            victim_type="confirmed_multiple",
            count_min=3,
            verification_status="verified",
        )
        denial = make_observation(
            "obs-social-denial",
            timestamp="2026-09-19T14:05:00Z",
            source_type="social",
            source_id="replay",
            author_id="denier",
            raw_text="Riverside Apartments",
            victim_type="none",
        )
        _, resolution = self._resolved(radio)
        created = self.fusion.fuse(radio, resolution, [])
        updated = self.fusion.fuse(denial, resolution, [created.incident])
        self.assertEqual(updated.incident["status"], "active")
        self.assertEqual(updated.incident["verification_status"], "verified")
        self.assertEqual(updated.incident["victim_state"]["indication"]["type"], "confirmed_multiple")

    def test_social_independence_uses_author_id(self):
        first = make_observation(
            "obs-post-a",
            raw_text="Riverside Apartments",
            source_type="social",
            source_id="replay",
            author_id="author-1",
        )
        same_author = make_observation(
            "obs-post-b",
            timestamp="2026-09-19T14:02:00Z",
            raw_text="Riverside Apartments",
            source_type="social",
            source_id="replay",
            author_id="author-1",
        )
        other_author = make_observation(
            "obs-post-c",
            timestamp="2026-09-19T14:03:00Z",
            raw_text="Riverside Apartments",
            source_type="social",
            source_id="replay",
            author_id="author-2",
        )
        unattributed_a = make_observation(
            "obs-post-d",
            raw_text="Hill Clinic",
            source_type="social",
            source_id="replay",
        )
        unattributed_b = make_observation(
            "obs-post-e",
            timestamp="2026-09-19T14:02:00Z",
            raw_text="Hill Clinic",
            source_type="social",
            source_id="replay",
        )
        self.assertEqual(
            independent_source_key(first)[2],
            independent_source_key(same_author)[2],
        )
        self.assertNotEqual(independent_source_key(first), independent_source_key(other_author))
        self.assertEqual(independent_source_key(unattributed_a), independent_source_key(unattributed_b))

        _, riverside = self._resolved(first)
        created = self.fusion.fuse(first, riverside, [])
        same = self.fusion.fuse(same_author, riverside, [created.incident])
        self.assertEqual(same.incident["verification_status"], "unverified")
        different = self.fusion.fuse(other_author, riverside, [created.incident])
        self.assertEqual(different.incident["verification_status"], "corroborated")

        _, hill = self._resolved(unattributed_a)
        platform = self.fusion.fuse(unattributed_a, hill, [])
        still_unverified = self.fusion.fuse(unattributed_b, hill, [platform.incident])
        self.assertEqual(still_unverified.incident["verification_status"], "unverified")

    def test_verified_drone_structural_outranks_radio(self):
        radio = make_observation(
            "obs-radio-severe",
            source_type="radio",
            source_id="rescue-alpha",
            raw_text="Riverside Apartments",
            incident_type="structural_damage",
            victim_type="unknown",
            structural_hazard="severe_collapse",
            verification_status="verified",
        )
        drone = make_observation(
            "obs-drone-minor",
            timestamp="2026-09-19T14:08:00Z",
            source_type="drone",
            source_id="drone-1",
            raw_text="Riverside Apartments",
            resolved_site_id="site-s1",
            incident_type="structural_damage",
            victim_type="unknown",
            structural_hazard="minor_damage",
            verification_status="verified",
        )
        _, resolution = self._resolved(radio)
        created = self.fusion.fuse(radio, resolution, [])
        updated = self.fusion.fuse(drone, self.resolver.resolve(drone, self.registry), [created.incident])
        self.assertEqual(updated.incident["structural_state"]["severity"], "minor")

    def test_fusion_window_uses_evidence_time_not_scenario_time(self):
        first = make_observation("obs-early", raw_text="Riverside Apartments", timestamp=T0)
        _, resolution = self._resolved(first)
        created = self.fusion.fuse(first, resolution, [], scenario_time="2026-09-19T16:00:00Z")
        self.assertEqual(created.incident["first_observed_at"], T0)
        self.assertEqual(created.incident["last_updated_at"], "2026-09-19T16:00:00Z")
        self.assertEqual(created.incident["metadata"]["latest_evidence_at"], T0)

        late = make_observation(
            "obs-two-hours",
            raw_text="Riverside Apartments",
            timestamp="2026-09-19T16:00:00Z",
            source_id="src-late",
        )
        result = self.fusion.fuse(late, resolution, [created.incident], scenario_time="2026-09-19T16:00:00Z")
        self.assertEqual(result.action, "created")
        self.assertNotEqual(result.incident["incident_id"], created.incident["incident_id"])

        inside = make_observation(
            "obs-inside-evidence",
            raw_text="Riverside Apartments",
            timestamp="2026-09-19T14:20:00Z",
            source_id="src-inside",
        )
        merged = self.fusion.fuse(inside, resolution, [created.incident], scenario_time="2026-09-19T16:00:00Z")
        self.assertEqual(merged.action, "updated")
        self.assertEqual(merged.incident["metadata"]["latest_evidence_at"], "2026-09-19T14:20:00Z")


if __name__ == "__main__":
    unittest.main()
