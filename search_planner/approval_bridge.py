"""JSON stdin/stdout bridge. The server persists records; clients cannot supply them."""
import json
import sys
from jsonschema.exceptions import ValidationError
from datetime import datetime, timezone
from .reassignment import initialize, step


def run(request):
    now = datetime.now(timezone.utc).isoformat()
    command = request['command']
    record = request.get('record')
    if command == 'initialize':
        if record is not None:
            raise ValueError('Search already initialized; publish an update instead')
        result = initialize(request['state'], now)
    elif record is None:
        raise ValueError('Publish an operational snapshot before using Search')
    elif command in {'tick', 'update', 'decide', 'report'}:
        result = step(record, now, state=request.get('state') if command == 'update' else None,
                      decision=request.get('decision') if command == 'decide' else None,
                      report=request.get('report') if command == 'report' else None)
    else:
        raise ValueError('Unknown Search command')
    return result


if __name__ == '__main__':
    try:
        print(json.dumps({'record': run(json.load(sys.stdin))}, allow_nan=False))
    except (ValueError, KeyError, TypeError, ValidationError) as exc:
        print(json.dumps({'error': str(exc)}))
