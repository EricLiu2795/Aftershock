"""LocationResolver: deterministic identity only, never invented Site IDs."""

import unittest

from operational.location_resolver import LocationResolver, ResolverConfig
from operational.site_registry import InMemorySiteRegistry
from operational.validation import validate_location_resolution
from tests.operational_fixtures import make_observation, make_site, test_registry


class LocationResolverTests(unittest.TestCase):
    def setUp(self):
        self.registry = test_registry()
        self.resolver = LocationResolver()

    def _resolve(self, **kwargs):
        observation = make_observation("obs-loc-1", **kwargs)
        result = self.resolver.resolve(observation, self.registry)
        validate_location_resolution(result)
        return result

    def test_exact_alias_resolves_one_site(self):
        result = self._resolve(raw_text="Riverside Apartments")
        self.assertEqual(result["resolution_status"], "resolved")
        self.assertEqual(result["resolved_site_id"], "site-s1")
        self.assertEqual(result["resolution_method"], "gazetteer")
        self.assertEqual(result["metadata"]["resolution_rule"], "exact_alias")
        self.assertEqual(result["metadata"]["raw_text"], "Riverside Apartments")
        self.assertNotEqual(result["resolved_site_id"], "Riverside Apartments")

    def test_normalized_alias_case_spacing_and_punctuation(self):
        result = self._resolve(raw_text="  riverside   apartments!! ")
        self.assertEqual(result["resolution_status"], "resolved")
        self.assertEqual(result["resolved_site_id"], "site-s1")
        self.assertEqual(result["metadata"]["resolution_rule"], "normalized_alias")

    def test_normalized_alias_is_accent_insensitive(self):
        result = self._resolve(raw_text="Rîverside Apartments")
        self.assertEqual(result["resolution_status"], "resolved")
        self.assertEqual(result["resolved_site_id"], "site-s1")

    def test_duplicate_alias_is_ambiguous(self):
        result = self._resolve(raw_text="Costa Brava", incident_type="structural_damage")
        self.assertEqual(result["resolution_status"], "ambiguous")
        self.assertIsNone(result["resolved_site_id"])
        self.assertEqual(set(result["candidate_site_ids"]), {"site-costa-a", "site-costa-b"})
        self.assertEqual(result["metadata"]["resolution_rule"], "duplicate_exact_alias")

    def test_coordinates_near_one_site_resolve(self):
        result = self._resolve(latitude=10.70000, longitude=-66.85000)
        self.assertEqual(result["resolution_status"], "resolved")
        self.assertEqual(result["resolved_site_id"], "site-far")
        self.assertEqual(result["resolution_method"], "nearest_site")

    def test_coordinates_equally_plausible_are_ambiguous(self):
        result = self._resolve(latitude=10.61000, longitude=-66.84990)
        self.assertEqual(result["resolution_status"], "ambiguous")
        self.assertIsNone(result["resolved_site_id"])
        self.assertEqual(set(result["candidate_site_ids"]), {"site-s1", "site-s-near"})
        self.assertEqual(result["metadata"]["resolution_rule"], "ambiguous_nearest_sites")

    def test_vague_area_name_is_unresolved(self):
        result = self._resolve(raw_text="the coast", accuracy="area")
        self.assertEqual(result["resolution_status"], "unresolved")
        self.assertIsNone(result["resolved_site_id"])
        self.assertEqual(result["candidate_site_ids"], [])

    def test_no_location_is_unresolved(self):
        result = self._resolve()
        self.assertEqual(result["resolution_status"], "unresolved")
        self.assertIn("no_location", result["metadata"]["notes"])

    def test_unknown_string_is_unresolved(self):
        result = self._resolve(raw_text="Totally Unknown Building 99")
        self.assertEqual(result["resolution_status"], "unresolved")
        self.assertIsNone(result["resolved_site_id"])
        self.assertNotIn("Totally Unknown Building 99", result["candidate_site_ids"])

    def test_trusted_canonical_site_id_resolves_directly(self):
        result = self._resolve(
            raw_text="this text is ignored",
            resolved_site_id="site-s1",
        )
        self.assertEqual(result["resolution_status"], "resolved")
        self.assertEqual(result["resolved_site_id"], "site-s1")
        self.assertEqual(result["resolution_method"], "manual")
        self.assertEqual(result["metadata"]["resolution_rule"], "trusted_site_id")

    def test_metadata_trusted_site_id_resolves(self):
        result = self._resolve(metadata={"trusted_site_id": "site-far"})
        self.assertEqual(result["resolved_site_id"], "site-far")
        self.assertEqual(result["metadata"]["resolution_rule"], "trusted_site_id")

    def test_invented_site_id_is_ignored_and_never_created(self):
        result = self._resolve(
            raw_text="Riverside Apartments",
            resolved_site_id="Riverside Apartments",
        )
        self.assertEqual(result["resolved_site_id"], "site-s1")
        self.assertNotEqual(result["resolved_site_id"], "Riverside Apartments")
        self.assertTrue(
            any("Riverside Apartments" in note for note in result["metadata"]["notes"])
        )

    def test_invented_site_id_alone_does_not_become_a_site(self):
        result = self._resolve(resolved_site_id="site-hallucinated")
        self.assertEqual(result["resolution_status"], "unresolved")
        self.assertIsNone(result["resolved_site_id"])
        self.assertNotIn("site-hallucinated", result["candidate_site_ids"])
        self.assertIsNone(self.registry.get_site("site-hallucinated"))

    def test_no_hallucinated_ids_on_any_path(self):
        known = {site["site_id"] for site in self.registry.list_sites()}
        samples = [
            {"raw_text": "Riverside Apartments"},
            {"raw_text": "costa brava"},
            {"raw_text": "nowhere"},
            {"latitude": 10.61, "longitude": -66.85},
            {"resolved_site_id": "not-a-real-site", "raw_text": "Hill Clinic"},
        ]
        for kwargs in samples:
            result = self._resolve(**kwargs)
            for site_id in [result["resolved_site_id"], *result["candidate_site_ids"]]:
                if site_id is not None:
                    self.assertIn(site_id, known)

    def test_alias_is_not_used_as_site_id_even_if_unique(self):
        registry = InMemorySiteRegistry.from_sites(
            [make_site("site-canonical-1", "Riverside Apartments", 10.5, -66.8, aliases=["Riverside Apartments"])],
            {"site-canonical-1": ["Riverside Apartments"]},
        )
        result = LocationResolver().resolve(
            make_observation("obs-x", raw_text="Riverside Apartments"),
            registry,
        )
        self.assertEqual(result["resolved_site_id"], "site-canonical-1")
        self.assertNotEqual(result["resolved_site_id"], "Riverside Apartments")

    def test_coordinates_outside_radius_unresolved(self):
        tight = LocationResolver(ResolverConfig(nearest_radius_m=5.0))
        result = tight.resolve(
            make_observation("obs-far", latitude=10.6105, longitude=-66.8500),
            self.registry,
        )
        self.assertEqual(result["resolution_status"], "unresolved")
        self.assertEqual(result["candidate_site_ids"], [])

    def test_conflicting_trusted_ids_are_ambiguous(self):
        result = self._resolve(
            resolved_site_id="site-s1",
            metadata={"trusted_site_id": "site-far"},
        )
        self.assertEqual(result["resolution_status"], "ambiguous")
        self.assertEqual(set(result["candidate_site_ids"]), {"site-s1", "site-far"})


if __name__ == "__main__":
    unittest.main()
