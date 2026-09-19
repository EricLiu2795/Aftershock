"""JSON stdin/stdout bridge. The server persists records; clients cannot supply them."""
import json
import sys
from jsonschema.exceptions import ValidationError
from datetime import datetime, timezone
from .operational_sync import (
    decide_search_sync,
    get_operational_snapshot,
    mark_synced,
    planning_snapshot,
    refresh_plan_and_dispatch_idle,
)
from .reassignment import initialize, step
from .verification import DemoVerification


def run(request):
    now = datetime.now(timezone.utc).isoformat()
    command = request['command']
    record = request.get('record')
    snapshot = request.get('operational_snapshot')
    if snapshot is None:
        snapshot = get_operational_snapshot(
            request.get('session_id'),
            sqlite_path=request.get('sqlite_path'),
        )
    scenario_id = request.get('scenario_id')
    verification = DemoVerification() if scenario_id in {'caraballeda', 'caraballeda-v0'} else None

    if command == 'initialize':
        if record is not None:
            raise ValueError('Search already initialized; publish an update instead')
        result = initialize(request['state'], now)
        return mark_synced(result, request['state']['state_version'], 'initialize')

    if record is None:
        if command == 'tick' and snapshot is not None:
            planning = planning_snapshot(snapshot, scenario_id=scenario_id)
            result = initialize(planning, now)
            return mark_synced(result, planning['state_version'], 'initialize')
        raise ValueError('Publish an operational snapshot before using Search')

    if command not in {'tick', 'update', 'decide', 'report'}:
        raise ValueError('Unknown Search command')

    state = request.get('state') if command == 'update' else None
    sync_action = 'update' if command == 'update' else 'tick'
    if command != 'update':
        decision = decide_search_sync(record, snapshot, scenario_id=scenario_id)
        if decision['action'] == 'update':
            state = decision['state']
            sync_action = 'update'
        elif decision['action'] == 'noop':
            sync_action = 'noop'

    result = step(
        record,
        now,
        state=state,
        decision=request.get('decision') if command == 'decide' else None,
        report=request.get('report') if command == 'report' else None,
        verification=verification,
    )
    if state is not None:
        result = refresh_plan_and_dispatch_idle(result)
        return mark_synced(result, state['state_version'], sync_action)
    result['operational_state_version'] = record.get(
        'operational_state_version', result['state']['state_version']
    )
    result['sync_action'] = sync_action
    return result


if __name__ == '__main__':
    try:
        print(json.dumps({'record': run(json.load(sys.stdin))}, allow_nan=False))
    except (ValueError, KeyError, TypeError, ValidationError) as exc:
        print(json.dumps({'error': str(exc)}))
