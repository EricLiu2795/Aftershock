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


if __name__ == "__main__":
    unittest.main()
