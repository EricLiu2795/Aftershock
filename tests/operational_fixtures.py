"""Shared builders for operational-state tests. Not scenario-pack data."""

from copy import deepcopy

from operational.ids import schema_version
from operational.site_registry import InMemorySiteRegistry


SCHEMA = schema_version()
T0 = "2026-09-19T14:00:00Z"


def make_site(
    site_id,
    display_name,
    lat,
    lon,
    *,
    aliases=None,
    site_type="building",
    criticality=0.5,
    address=None,
):
    return {
        "schema_version": SCHEMA,
        "site_id": site_id,
        "site_type": site_type,
        "display_name": display_name,
        "canonical_source": "manual",
        "canonical_source_id": site_id,
        "address": address,
        "centroid": {"latitude": lat, "longitude": lon},
        "geometry_reference": None,
        "facility_type": "residential",
        "criticality": criticality,
        "metadata": {"aliases": list(aliases or [])},
    }


def make_observation(
    observation_id,
    *,
    timestamp=T0,
    source_type="social",
    source_id="src-1",
    raw_text=None,
    label=None,
    latitude=None,
    longitude=None,
    accuracy=None,
    resolved_site_id=None,
    incident_type="trapped_victims",
    victim_type="possible_multiple",
    count_min=None,
    count_max=None,
    structural_hazard="unknown",
    access_status="unknown",
    verification_status="unverified",
    metadata=None,
):
    return {
        "schema_version": SCHEMA,
        "observation_id": observation_id,
        "timestamp": timestamp,
        "source_type": source_type,
        "source_id": source_id,
        "location": {
            "latitude": latitude,
            "longitude": longitude,
            "raw_text": raw_text,
            "label": label,
            "accuracy": accuracy,
            "resolved_site_id": resolved_site_id,
        },
        "incident_type": incident_type,
        "victim_indication": {
            "type": victim_type,
            "count_min": count_min,
            "count_max": count_max,
        },
        "structural_hazard": structural_hazard,
        "access_status": access_status,
        "verification_status": verification_status,
        "raw_source_reference": {
            "reference_type": "test",
            "value": observation_id,
        },
        "metadata": deepcopy(metadata) if metadata is not None else {"extracted_by": "test"},
    }


def test_registry():
    riverside = make_site(
        "site-s1",
        "Riverside Apartments",
        10.61000,
        -66.85000,
        aliases=["Riverside Apartments"],
        criticality=0.8,
    )
    costa_a = make_site(
        "site-costa-a",
        "Costa Brava North",
        10.62000,
        -66.84000,
        aliases=["Costa Brava"],
        criticality=0.4,
    )
    costa_b = make_site(
        "site-costa-b",
        "Costa Brava South",
        10.62100,
        -66.84100,
        aliases=["Costa Brava"],
        criticality=0.4,
    )
    neighbor = make_site(
        "site-s-near",
        "Harbor Warehouse",
        10.61000,
        -66.84980,
        aliases=["Harbor Warehouse"],
        criticality=0.3,
    )
    far = make_site(
        "site-far",
        "Hill Clinic",
        10.70000,
        -66.85000,
        aliases=["Hill Clinic"],
        criticality=0.2,
    )
    return InMemorySiteRegistry.from_sites(
        [riverside, costa_a, costa_b, neighbor, far],
        {
            "site-s1": ["Riverside Apartments"],
            "site-costa-a": ["Costa Brava"],
            "site-costa-b": ["Costa Brava"],
            "site-s-near": ["Harbor Warehouse"],
            "site-far": ["Hill Clinic"],
        },
    )
