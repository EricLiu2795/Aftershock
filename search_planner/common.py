"""Deterministic JSON, time, IDs and canonical contract validation."""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from ingestion.validation import validator, schema_documents


def validate(schema, payload):
    json.dumps(payload, allow_nan=False)
    validator(schema).validate(payload)


def version():
    return schema_documents()['common.schema.json']['$defs']['schemaVersion']['const']


def number(value, name, minimum=0, maximum=None):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        raise ValueError(f'{name} must be a finite number')
    if value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f'{name} outside allowed range')
    return float(value)


def time(value):
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.tzinfo is None: raise ValueError('A timezone is required')
    return parsed.astimezone(timezone.utc)


def stamp(value):
    parsed=time(value) if isinstance(value,str) else value
    return parsed.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00','Z')


def identity(prefix, value):
    raw=json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)
    return prefix+':'+sha256(raw.encode()).hexdigest()[:24]


def index(items, key):
    result={}
    for item in items:
        if item[key] in result: raise ValueError('Duplicate '+key+': '+item[key])
        result[item[key]]=item
    return result


def fingerprint(state):
    copy=deepcopy(state)
    keys={'sites':'site_id','worksites':'worksite_id','incidents':'incident_id','resources':'resource_id',
          'road_states':'road_id','movement_states':'resource_id','active_routes':'route_id',
          'pending_reassignment_proposals':'proposal_id'}
    for collection,key in keys.items():
        index(copy[collection],key)
        copy[collection].sort(key=lambda x:x[key])
    return identity('state',copy)
