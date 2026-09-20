"""JSON stdin/stdout bridge. The server persists records; clients cannot supply them."""
import json
import sys
from jsonschema.exceptions import ValidationError
from datetime import datetime, timezone
from .drone_feedback import operational_observations_from_missions
from .operational_sync import (
    decide_search_sync,
    get_operational_snapshot,
    has_actionable_worksite,
    mark_synced,
    planning_snapshot,
    refresh_plan_and_dispatch_idle,
)
from .reassignment import initialize, step
from .verification import DemoVerification

CARABALLEDA = {'caraballeda', 'caraballeda-v0'}


def _from_belief(snapshot, now, scenario_id):
    """Canonical initialize: plan from OperationalBeliefState and dispatch idle drones."""
    state = planning_snapshot(snapshot, scenario_id=scenario_id)
    result = initialize(state, now)
    result['tracking_demo'] = False
    result['scanning_enabled'] = True
    result = refresh_plan_and_dispatch_idle(result)
    return mark_synced(result, snapshot.get('state_version') or state['state_version'], 'initialize')


def run(request):
    # Trusted API-owned demo clock excludes paused wall time; CLI callers keep wall time.
    now = request.get('demo_now') or datetime.now(timezone.utc).isoformat()
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
    canonical = has_actionable_worksite(snapshot)
    if canonical and scenario_id in CARABALLEDA:
        # Keep canonical identities/lifecycle, with the demo's existing priority inputs.
        from copy import deepcopy
        from .building_priority import enrich_worksite
        from .scenario_buildings import scenario_buildings
        snapshot = deepcopy(snapshot)
        scenario = buildings if buildings is not None else scenario_buildings()
        stored = {r['site_id']: r for r in request.get('stored_inspections', [])}
        for work in snapshot['worksites']:
            observations = [o for item in request.get('intake', [])
                            if item['site']['site_id'] == work['site_id'] for o in item['observations']]
            enrich_worksite(work, observations, stored.get(work['site_id']), scenario.get(work['site_id']))

    if command == 'prepare':
        if record is not None:
            return record
        # The dashboard calls prepare whenever Search is uninitialized; the standalone
        # harness may only claim a session that canonical belief cannot yet drive.
        if canonical:
            return _finish(request, command, _from_belief(snapshot, now, scenario_id), buildings)
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
        if command == 'tick' and canonical:
            return _finish(request, command, _from_belief(snapshot, now, scenario_id), buildings)
        if command == 'tick':
            return {'uninitialized': True, 'waiting_reason': 'NO_CANONICAL_WORKSITE', 'last_wall_at': now,
                    'proposals': [], 'missions': [], 'operational_observations': []}
        raise ValueError('Publish an operational snapshot before using Search')

    if command not in {'tick', 'update', 'decide', 'report', 'dispatch', 'speed'}:
        raise ValueError('Unknown Search command')

    state = request.get('state') if command == 'update' else None
    sync_action = 'update' if command == 'update' else 'tick'
    synced = False
    # Canonical belief outranks the standalone harness: one actionable Worksite is enough to
    # put a prepared tracking record back on OperationalBeliefState without an operator click.
    if command != 'update' and (canonical or not record.get('tracking_demo')):
        decision = decide_search_sync(record, snapshot, scenario_id=scenario_id)
        if decision['action'] == 'update':
            state = decision['state']
            sync_action = 'update'
            synced = True
        elif decision['action'] == 'noop':
            sync_action = 'noop'

    verification = None
    if scenario_id in CARABALLEDA:
        # Simulated outcomes stay hidden until DemoVerification sees a physically completed scan.
        demo = bool(record.get('tracking_demo') or record.get('scanning_enabled'))
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
        if synced:
            result['scanning_enabled'] = True
    else:
        result['operational_state_version'] = record.get(
            'operational_state_version', result['state']['state_version']
        )
        result['sync_action'] = sync_action
    if state is None and result.get('scanning_enabled') and not result.get('tracking_demo'):
        # A drone that just finished a scan takes its next canonical target on the same tick,
        # even when belief did not change and therefore produced no replan.
        result = refresh_plan_and_dispatch_idle(result)
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
    if result.get('tracking_demo') or result.get('scanning_enabled'):
        from .coverage import publish_priorities
        result = publish_priorities(result)
    result['operational_observations'] = operational_observations_from_missions(
        list(result.get('missions') or []) + [h['mission'] for h in result.get('history', [])])
    return result


if __name__ == '__main__':
    try:
        print(json.dumps({'record': run(json.load(sys.stdin))}, allow_nan=False))
    except (ValueError, KeyError, TypeError, ValidationError) as exc:
        print(json.dumps({'error': str(exc)}))
