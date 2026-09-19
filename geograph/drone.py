"""Structured infrared drone reports -> existing observation contract. No CV/LLM."""
from copy import deepcopy
import hashlib
from .store import encode, validate, version


def normalize_drone(report):
    validate("drone_observation_input.schema.json",report)
    damage = report.get("structural_damage") or "unknown"
    access = report.get("access_status") or "unknown"
    victim = report.get("victim_indicator") or {"type":"unknown"}
    kinds = []
    if damage not in {"unknown","none"}: kinds.append("structural_damage" if damage!="active_secondary_hazard" else "fire_or_secondary_hazard")
    if victim["type"] not in {"unknown","none"}: kinds.append("trapped_victims")
    if access in {"blocked","possibly_blocked","limited"}: kinds.append("access_blocked")
    # The caller can name existing canonical categories for explicit negative findings.
    supplied = report.get("metadata",{}).get("assessed_incident_types",[])
    allowed = {"structural_damage","trapped_victims","access_blocked"}
    if not isinstance(supplied,list) or any(k not in allowed for k in supplied):
        raise ValueError("assessed_incident_types supports structural_damage, trapped_victims, access_blocked")
    kinds = sorted(set(kinds+supplied))
    result = []
    for kind in kinds:
        item = {"schema_version":version(),"observation_id":"drone:"+hashlib.sha256((report["input_id"]+":"+kind).encode()).hexdigest()[:24],
                "timestamp":report["timestamp"],"source_type":"drone","source_id":report["drone_id"],
                "location":deepcopy(report["location"]),"incident_type":kind,"victim_indication":deepcopy(victim),
                "structural_hazard":damage,"access_status":access,"verification_status":"unverified",
                "raw_source_reference":{"reference_type":"drone_json","value":report["input_id"]},
                "metadata":{"sensor_type":"infrared","bypassed_llm":True,"raw_report":deepcopy(report)}}
        # Sensor type alone never upgrades a claim to verified.
        validate("observation.schema.json",item)
        result.append(item)
    return result


def ingest_drone(store, report, resolved_site_id=None):
    observations = normalize_drone(report)
    with store.transaction():
        store._record("drone_input",report["input_id"],report,report["timestamp"])
        for item in observations:
            store.observation(item,resolved_site_id)
        road = report.get("metadata",{}).get("road_assessment")
        if road is not None:
            if "conditions" in road:
                raise ValueError("Detailed road conditions are no longer supported; use access_status and passability")
            # Explicit road identity and per-profile assessment supplied by the sensor/operator.
            # Never infer a road from a nearby building or upgrade possibly_blocked to blocked.
            if report.get("access_status") not in {"open","limited","blocked"}:
                raise ValueError("A drone road assessment requires explicit open/limited/blocked access_status")
            observation_id = next((o["observation_id"] for o in observations if o["incident_type"]=="access_blocked"),None)
            if observation_id is None:
                explicit = deepcopy(report)
                explicit.setdefault("metadata",{})["assessed_incident_types"]=["access_blocked"]
                negative = normalize_drone(explicit)
                for item in negative:
                    if item["incident_type"]=="access_blocked":
                        # Keep actual raw source payload; the generated negative finding was explicit.
                        item["metadata"]["raw_report"] = deepcopy(report)
                        store.observation(item,resolved_site_id)
                        observations.append(item)
                        observation_id=item["observation_id"]
            evidence_id="evidence:"+hashlib.sha256(report["input_id"].encode()).hexdigest()[:24]
            value={"schema_version":version(),"assessment_id":"drone-road:"+report["input_id"],
                   "road_state":{"schema_version":version(),"road_id":road["road_id"],
                                 "status":report["access_status"],"travel_cost_multiplier":road.get("travel_cost_multiplier",1),
                                 "updated_at":report["timestamp"],"evidence_ids":[evidence_id]},
                   "passability":road["passability"],"verification_status":"unverified",
                   "evidence":[{"schema_version":version(),"evidence_id":evidence_id,"observation_id":observation_id,
                                "source_type":"drone","source_id":report["drone_id"],"timestamp":report["timestamp"],
                                "summary":"Structured infrared drone road report", "verification_status":"unverified"}]}
            store.road_assessment(value)
    return observations
