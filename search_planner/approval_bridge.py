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
    if command == 'prepare':
        if record is not None:
            return record
        if request.get('scenario_id') not in {'caraballeda', 'caraballeda-v0'}:
            raise ValueError('Tracking requires a Caraballeda session')
        from .tracking_demo import prepare_tracking, sync_intake
        result = sync_intake(prepare_tracking(now, request['session_id']), request.get('intake', []))
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
                      verification=DemoVerification() if request.get('scenario_id') in {'caraballeda', 'caraballeda-v0'} else None)
    else:
        raise ValueError('Unknown Search command')
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
            result = sync_intake(result, request.get('intake', []))
            if command == 'dispatch' or result.get('scanning_enabled'):
                result = dispatch_tracking(result)
    return result


if __name__ == '__main__':
    try:
        print(json.dumps({'record': run(json.load(sys.stdin))}, allow_nan=False))
    except (ValueError, KeyError, TypeError, ValidationError) as exc:
        print(json.dumps({'error': str(exc)}))
