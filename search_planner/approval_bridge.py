"""JSON stdin/stdout bridge. The server persists records; clients cannot supply them."""
import json
import sys
from jsonschema.exceptions import ValidationError
from datetime import datetime, timezone
from .reassignment import initialize, step
from .verification import DemoVerification


def run(request):
    now = datetime.now(timezone.utc).isoformat()
    command = request['command']
    record = request.get('record')
    # Building facts (people counts, photos) come from the server's SQLite table when supplied.
    buildings = None
    if request.get('scenario_buildings') is not None:
        from .scenario_buildings import buildings_from_rows
        buildings = buildings_from_rows(request['scenario_buildings'])
    if command == 'prepare':
        if record is not None:
            return record
        if request.get('scenario_id') not in {'caraballeda', 'caraballeda-v0'}:
            raise ValueError('Tracking requires a Caraballeda session')
        from .tracking_demo import prepare_tracking, sync_intake
        result = sync_intake(prepare_tracking(now, request['session_id']), request.get('intake', []), request.get('stored_inspections', []), buildings)
    elif command == 'initialize':
        if record is not None:
            raise ValueError('Search already initialized; publish an update instead')
        result = initialize(request['state'], now)
    elif record is None:
        raise ValueError('Publish an operational snapshot before using Search')
    elif command in {'tick', 'update', 'decide', 'report', 'dispatch', 'speed'}:
        result = step(record, now, state=request.get('state') if command == 'update' else None,
                      decision=request.get('decision') if command == 'decide' else None,
                      report=request.get('report') if command == 'report' else None,
                      verification=DemoVerification(stored_inspections=request.get('stored_inspections', []), complete_missing=bool(record.get('tracking_demo')), scenario_outcomes=bool(record.get('tracking_demo')),
                                                    buildings=buildings if record.get('tracking_demo') else None) if request.get('scenario_id') in {'caraballeda', 'caraballeda-v0'} else None)
    else:
        raise ValueError('Unknown Search command')
    if record is not None and request.get('scenario_id') in {'caraballeda', 'caraballeda-v0'}:
        from .road_scan import scan_roads
        result = scan_roads(record, result)
    if command == 'speed':
        speed = request.get('simulation_speed')
        if type(speed) not in (int, float) or speed not in (1, 5, 10):
            raise ValueError('Simulation speed must be 1, 5, or 10')
        result['simulation_speed'] = speed
    if result.get('tracking_demo'):
        from .tracking_demo import sync_intake, dispatch_tracking
        if command == 'update':
            result['tracking_demo'] = False
        else:
            result = sync_intake(result, request.get('intake', []), request.get('stored_inspections', []), buildings)
            from .planner import plan_search
            result['latest_search_plan']=plan_search(result['state'])
            if command == 'dispatch' or result.get('scanning_enabled'):
                result = dispatch_tracking(result)
    if result.get('tracking_demo'):
        from .tracking_demo import publish_priorities
        result = publish_priorities(result)
    return result


if __name__ == '__main__':
    try:
        print(json.dumps({'record': run(json.load(sys.stdin))}, allow_nan=False))
    except (ValueError, KeyError, TypeError, ValidationError) as exc:
        print(json.dumps({'error': str(exc)}))
