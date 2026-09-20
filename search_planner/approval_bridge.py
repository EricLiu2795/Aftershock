"""JSON stdin/stdout bridge. The server persists records; clients cannot supply them."""
import json
import sys
from jsonschema.exceptions import ValidationError
from datetime import datetime, timezone
from .drone_feedback import operational_observations_from_missions
from .operational_sync import (
    decide_search_sync,
    get_operational_snapshot,
    mark_synced,
    planning_snapshot,
    refresh_plan_and_dispatch_idle,
)
from .reassignment import initialize, step
from .verification import DemoVerification

CARABALLEDA = {'caraballeda', 'caraballeda-v0'}


def run(request):
    now = datetime.now(timezone.utc).isoformat()
    command = request['command']
    record = request.get('record')
    # Building facts (people counts, photos) come from the server's SQLite table when supplied.
    buildings = None
    if request.get('scenario_buildings') is not None:
        from .scenario_buildings import buildings_from_rows
        buildings = buildings_from_rows(request['scenario_buildings'])
    snapshot = request.get('operational_snapshot')
    if snapshot is None:
        snapshot = get_operational_snapshot(
            request.get('session_id'),
            sqlite_path=request.get('sqlite_path'),
        )
    scenario_id = request.get('scenario_id')

    if command == 'prepare':
        if record is not None:
            return record
        if scenario_id not in CARABALLEDA:
            raise ValueError('Tracking requires a Caraballeda session')
        from .tracking_demo import prepare_tracking, sync_intake
        result = sync_intake(prepare_tracking(now, request['session_id']), request.get('intake', []),
                             request.get('stored_inspections', []), buildings)
        return _finish(request, command, result, buildings)

    if command == 'initialize':
        if record is not None:
            raise ValueError('Search already initialized; publish an update instead')
        state = request.get('state')
        if state is None and snapshot is not None:
            state = planning_snapshot(snapshot, scenario_id=scenario_id)
        if state is None:
            raise ValueError('Publish an operational snapshot before using Search')
        result = initialize(state, now)
        synced_version = state['state_version']
        if snapshot is not None and snapshot.get('state_version'):
            synced_version = snapshot['state_version']
        return _finish(request, command, mark_synced(result, synced_version, 'initialize'), buildings)

    if record is None:
        if command == 'tick' and snapshot is not None:
            planning = planning_snapshot(snapshot, scenario_id=scenario_id)
            result = initialize(planning, now)
            return _finish(request, command, mark_synced(result, planning['state_version'], 'initialize'), buildings)
        if command == 'tick':
            return {'uninitialized': True, 'last_wall_at': now, 'proposals': [], 'missions': [],
                    'operational_observations': []}
        raise ValueError('Publish an operational snapshot before using Search')

    if command not in {'tick', 'update', 'decide', 'report', 'dispatch', 'speed'}:
        raise ValueError('Unknown Search command')

    state = request.get('state') if command == 'update' else None
    sync_action = 'update' if command == 'update' else 'tick'
    # A prepared tracking demo owns its Worksites (imported reports + SQLite building facts) and
    # its explicit dispatch gate. Belief-snapshot sync drives Search only for non-demo records;
    # demo scan findings still reach Agent 1 through `operational_observations`.
    if command != 'update' and not record.get('tracking_demo'):
        decision = decide_search_sync(record, snapshot, scenario_id=scenario_id)
        if decision['action'] == 'update':
            state = decision['state']
            sync_action = 'update'
        elif decision['action'] == 'noop':
            sync_action = 'noop'

    verification = None
    if scenario_id in CARABALLEDA:
        demo = bool(record.get('tracking_demo'))
        verification = DemoVerification(stored_inspections=request.get('stored_inspections', []),
                                        complete_missing=demo, scenario_outcomes=demo,
                                        buildings=buildings if demo else None)

    result = step(
        record,
        now,
        state=state,
        decision=request.get('decision') if command == 'decide' else None,
        report=request.get('report') if command == 'report' else None,
        verification=verification,
    )
    if scenario_id in CARABALLEDA:
        from .road_scan import scan_roads
        result = scan_roads(record, result)
    if state is not None:
        result = refresh_plan_and_dispatch_idle(result)
        result = mark_synced(result, state['state_version'], sync_action)
        if snapshot is not None:
            result['tracking_demo'] = False
    else:
        result['operational_state_version'] = record.get(
            'operational_state_version', result['state']['state_version']
        )
        result['sync_action'] = sync_action
    return _finish(request, command, result, buildings)


def _finish(request, command, result, buildings=None):
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
            result['latest_search_plan'] = plan_search(result['state'])
            if command == 'dispatch' or result.get('scanning_enabled'):
                result = dispatch_tracking(result)
    if result.get('tracking_demo'):
        from .tracking_demo import publish_priorities
        result = publish_priorities(result)
    result['operational_observations'] = operational_observations_from_missions(result.get('missions') or [])
    return result


if __name__ == '__main__':
    try:
        print(json.dumps({'record': run(json.load(sys.stdin))}, allow_nan=False))
    except (ValueError, KeyError, TypeError, ValidationError) as exc:
        print(json.dumps({'error': str(exc)}))
