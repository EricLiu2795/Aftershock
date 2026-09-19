"""Worksite materialization aggregates Incidents without mixing evidence state."""

import unittest

from operational.incident_fusion import IncidentFusion
from operational.location_resolver import LocationResolver
from operational.validation import validate_worksite
from operational.worksite import materialize_worksite
from tests.operational_fixtures import make_observation, test_registry


class WorksiteTests(unittest.TestCase):
    def setUp(self):
        self.registry = test_registry()
        self.resolver = LocationResolver()
        self.fusion = IncidentFusion()
        self.site = self.registry.get_site("site-s1")

    def _incident(self, **kwargs):
        observation = make_observation(**kwargs)
        resolution = self.resolver.resolve(observation, self.registry)
        result = self.fusion.fuse(observation, resolution, [])
        self.assertEqual(result.action, "created")
        return result.incident

    def test_multiple_incident_types_aggregate(self):
        trapped = self._incident(
            observation_id="obs-t",
            raw_text="Riverside Apartments",
            incident_type="trapped_victims",
            victim_type="possible_multiple",
        )
        structural = self._incident(
            observation_id="obs-s",
            raw_text="Riverside Apartments",
            incident_type="structural_damage",
            victim_type="unknown",
            structural_hazard="major_damage",
        )
        access = self._incident(
            observation_id="obs-a",
            raw_text="Riverside Apartments",
            incident_type="access_blocked",
            victim_type="unknown",
            access_status="blocked",
        )
        worksite = materialize_worksite(
            self.site,
            [trapped, structural, access],
            updated_at="2026-09-19T14:20:00Z",
            last_observation_id="obs-a",
        )
        validate_worksite(worksite)
        self.assertEqual(worksite["site_id"], "site-s1")
        self.assertEqual(worksite["worksite_id"], "worksite-s1")
        self.assertEqual(set(worksite["active_incident_ids"]), {
            trapped["incident_id"],
            structural["incident_id"],
            access["incident_id"],
        })
        self.assertEqual(worksite["victim_summary"]["indication"]["type"], "possible_multiple")
        self.assertEqual(worksite["structural_summary"]["severity"], "major")
        self.assertEqual(worksite["access_summary"]["status"], "blocked")
        self.assertEqual(set(worksite["evidence_ids"]), {
            trapped["evidence_ids"][0],
            structural["evidence_ids"][0],
            access["evidence_ids"][0],
        })
        self.assertNotIn("search_priority", worksite)
        self.assertNotIn("save_priority", worksite)
        self.assertEqual(worksite["assigned_resource_ids"], [])

    def test_evidence_remains_traceable(self):
        trapped = self._incident(
            observation_id="obs-t",
            raw_text="Riverside Apartments",
            source_type="social",
            source_id="post-1",
        )
        worksite = materialize_worksite(
            self.site,
            [trapped],
            updated_at=trapped["last_updated_at"],
            last_observation_id="obs-t",
        )
        self.assertEqual(worksite["evidence_ids"], trapped["evidence_ids"])
        self.assertEqual(worksite["metadata"]["contributing_incident_ids"], [trapped["incident_id"]])
        self.assertEqual(worksite["metadata"]["last_observation_id"], "obs-t")

    def test_operational_status_independent_from_verification_state(self):
        trapped = self._incident(
            observation_id="obs-t",
            raw_text="Riverside Apartments",
            source_type="social",
        )
        worksite = materialize_worksite(
            self.site,
            [trapped],
            updated_at=trapped["last_updated_at"],
        )
        self.assertEqual(worksite["operational_status"], "pending")
        self.assertEqual(worksite["evidence_status"], "unverified")

        assigned = dict(worksite)
        assigned["operational_status"] = "assigned"
        assigned["assigned_resource_ids"] = ["rescue-bravo"]
        radio = make_observation(
            "obs-r",
            timestamp="2026-09-19T14:10:00Z",
            source_type="radio",
            source_id="rescue-alpha",
            raw_text="Riverside Apartments",
            victim_type="confirmed_multiple",
            count_min=4,
        )
        resolution = self.resolver.resolve(radio, self.registry)
        updated_incident = self.fusion.fuse(radio, resolution, [trapped]).incident
        rematerialized = materialize_worksite(
            self.site,
            [updated_incident],
            updated_at=updated_incident["last_updated_at"],
            previous=assigned,
        )
        self.assertEqual(rematerialized["operational_status"], "assigned")
        self.assertEqual(rematerialized["assigned_resource_ids"], ["rescue-bravo"])
        self.assertEqual(rematerialized["evidence_status"], "corroborated")
        self.assertNotEqual(rematerialized["operational_status"], rematerialized["evidence_status"])

    def test_access_none_victims_do_not_overwrite_trapped_summary(self):
        trapped = self._incident(
            observation_id="obs-t",
            raw_text="Riverside Apartments",
            incident_type="trapped_victims",
            victim_type="confirmed_multiple",
            count_min=4,
        )
        access = self._incident(
            observation_id="obs-a",
            raw_text="Riverside Apartments",
            incident_type="access_blocked",
            victim_type="none",
            access_status="blocked",
        )
        worksite = materialize_worksite(
            self.site,
            [trapped, access],
            updated_at="2026-09-19T14:20:00Z",
        )
        self.assertEqual(worksite["victim_summary"]["indication"]["type"], "confirmed_multiple")
        self.assertEqual(worksite["access_summary"]["status"], "blocked")

    def test_new_worksite_does_not_invent_priorities_or_assignments(self):
        trapped = self._incident(
            observation_id="obs-t",
            raw_text="Riverside Apartments",
        )
        worksite = materialize_worksite(
            self.site,
            [trapped],
            updated_at=trapped["last_updated_at"],
        )
        self.assertEqual(worksite["criticality"], self.site["criticality"])
        self.assertEqual(worksite["display_name"], self.site["display_name"])

    def test_later_drone_outranks_old_social_structural_severity(self):
        social = self._incident(
            observation_id="obs-old-social",
            raw_text="Riverside Apartments",
            incident_type="structural_damage",
            victim_type="unknown",
            structural_hazard="severe_collapse",
            source_type="social",
            source_id="replay",
            author_id="author-old",
        )
        drone = make_observation(
            "obs-later-drone",
            timestamp="2026-09-19T14:12:00Z",
            source_type="drone",
            source_id="drone-1",
            raw_text="Riverside Apartments",
            resolved_site_id="site-s1",
            incident_type="structural_damage",
            victim_type="unknown",
            structural_hazard="minor_damage",
        )
        updated = self.fusion.fuse(drone, self.resolver.resolve(drone, self.registry), [social]).incident
        worksite = materialize_worksite(
            self.site,
            [updated],
            updated_at=updated["last_updated_at"],
        )
        self.assertEqual(updated["structural_state"]["severity"], "minor")
        self.assertEqual(worksite["structural_summary"]["severity"], "minor")

        intact = make_observation(
            "obs-drone-none",
            timestamp="2026-09-19T14:13:00Z",
            source_type="drone",
            source_id="drone-1",
            raw_text="Riverside Apartments",
            resolved_site_id="site-s1",
            incident_type="structural_damage",
            victim_type="unknown",
            structural_hazard="none",
        )
        none_updated = self.fusion.fuse(intact, self.resolver.resolve(intact, self.registry), [updated]).incident
        none_worksite = materialize_worksite(self.site, [none_updated], updated_at=none_updated["last_updated_at"])
        self.assertEqual(none_worksite["structural_summary"]["severity"], "none")

    def test_later_drone_open_outranks_social_blocked_at_worksite(self):
        social = self._incident(
            observation_id="obs-social-blocked",
            raw_text="Riverside Apartments",
            incident_type="access_blocked",
            victim_type="unknown",
            access_status="blocked",
            source_type="social",
            source_id="replay",
            author_id="author-block",
        )
        drone = make_observation(
            "obs-field-open",
            timestamp="2026-09-19T14:09:00Z",
            source_type="drone",
            source_id="drone-2",
            raw_text="Riverside Apartments",
            resolved_site_id="site-s1",
            incident_type="access_blocked",
            victim_type="unknown",
            access_status="open",
        )
        updated = self.fusion.fuse(drone, self.resolver.resolve(drone, self.registry), [social]).incident
        worksite = materialize_worksite(self.site, [updated], updated_at=updated["last_updated_at"])
        self.assertEqual(worksite["access_summary"]["status"], "open")
        self.assertEqual(updated["status"], "active")

    def test_invalidated_incident_excluded_but_worksite_and_evidence_remain(self):
        rumor = self._incident(
            observation_id="obs-w7-social",
            raw_text="Riverside Apartments",
            source_id="replay",
            author_id="rumor-author",
        )
        previous = materialize_worksite(self.site, [rumor], updated_at=rumor["last_updated_at"])
        drone = make_observation(
            "obs-w7-drone",
            timestamp="2026-09-19T14:10:00Z",
            source_type="drone",
            source_id="drone-2",
            raw_text="Riverside Apartments",
            resolved_site_id="site-s1",
            victim_type="none",
            count_min=0,
            count_max=0,
            verification_status="verified",
        )
        invalidated = self.fusion.fuse(drone, self.resolver.resolve(drone, self.registry), [rumor]).incident
        self.assertEqual(invalidated["status"], "invalidated")
        worksite = materialize_worksite(
            self.site,
            [invalidated],
            updated_at=invalidated["last_updated_at"],
            previous=previous,
        )
        self.assertEqual(worksite["active_incident_ids"], [])
        self.assertEqual(worksite["worksite_id"], previous["worksite_id"])
        self.assertEqual(worksite["operational_status"], "resolved")
        self.assertEqual(set(worksite["evidence_ids"]), set(invalidated["evidence_ids"]))
        self.assertEqual(worksite["metadata"]["invalidated_incident_ids"], [invalidated["incident_id"]])
        self.assertEqual(worksite["victim_summary"]["indication"]["type"], "unknown")

    def test_assigned_worksite_stays_assigned_while_an_incident_remains_active(self):
        trapped = self._incident(
            observation_id="obs-assigned",
            raw_text="Riverside Apartments",
        )
        previous = materialize_worksite(self.site, [trapped], updated_at=trapped["last_updated_at"])
        assigned = dict(previous)
        assigned["operational_status"] = "assigned"
        assigned["assigned_resource_ids"] = ["rescue-bravo"]
        rematerialized = materialize_worksite(
            self.site,
            [trapped],
            updated_at="2026-09-19T14:20:00Z",
            previous=assigned,
        )
        self.assertEqual(rematerialized["operational_status"], "assigned")
        self.assertEqual(rematerialized["assigned_resource_ids"], ["rescue-bravo"])
        self.assertEqual(rematerialized["active_incident_ids"], [trapped["incident_id"]])

    def test_assigned_worksite_resolves_after_complete_invalidation(self):
        trapped = self._incident(
            observation_id="obs-assigned-then-cleared",
            raw_text="Riverside Apartments",
        )
        previous = materialize_worksite(self.site, [trapped], updated_at=trapped["last_updated_at"])
        assigned = dict(previous)
        assigned["operational_status"] = "assigned"
        assigned["assigned_resource_ids"] = ["rescue-bravo"]
        drone = make_observation(
            "obs-assigned-drone-none",
            timestamp="2026-09-19T14:10:00Z",
            source_type="drone",
            source_id="drone-2",
            raw_text="Riverside Apartments",
            resolved_site_id="site-s1",
            victim_type="none",
            count_min=0,
            count_max=0,
            verification_status="verified",
        )
        invalidated = self.fusion.fuse(drone, self.resolver.resolve(drone, self.registry), [trapped]).incident
        rematerialized = materialize_worksite(
            self.site,
            [invalidated],
            updated_at=invalidated["last_updated_at"],
            previous=assigned,
        )
        self.assertEqual(invalidated["status"], "invalidated")
        self.assertEqual(rematerialized["operational_status"], "resolved")
        self.assertEqual(rematerialized["assigned_resource_ids"], ["rescue-bravo"])
        self.assertEqual(rematerialized["active_incident_ids"], [])
        self.assertEqual(set(rematerialized["evidence_ids"]), set(invalidated["evidence_ids"]))


if __name__ == "__main__":
    unittest.main()
