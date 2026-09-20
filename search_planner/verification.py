"""Scan-gated demo results. Interactive tracking may explicitly use scenario outcomes;
normal coordinator missions retain the authored-event provider.
"""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
from .common import identity, index, stamp, time, validate
from .runtime import advance_mission

PACK = Path(__file__).resolve().parents[1] / 'data/scenarios/caraballeda'


class DemoVerification:
    def __init__(self, pack=PACK, stored_inspections=(), complete_missing=False, scenario_outcomes=False, buildings=None):
        from .scenario_buildings import scenario_buildings
        # `buildings` are database rows supplied by the API; the pack JSON is only a fallback.
        self.scenario = buildings if buildings is not None else scenario_buildings(pack) if scenario_outcomes else {}
        self.complete_missing = complete_missing
        self.stored = {r['site_id']:r for r in stored_inspections}
        pack = Path(pack)
        self.events = json.loads((pack / 'scenario_events.json').read_text())['events']
        self.assets = index(json.loads((pack / 'verification_assets.json').read_text())['assets'], 'asset_id')

    def advance(self, mission, now, *, defer_return=False):
        """Same result for small ticks or one late tick; manual reports take precedence."""
        if mission['drone_report'] is not None:
            return advance_mission(mission, now, defer_return=defer_return)
        scan_end = time(mission['launched_at']) + timedelta(
            seconds=mission['outbound_route']['estimated_travel_time_s'] + mission['config']['verification_duration_s'])
        if time(now) < scan_end:
            return advance_mission(mission, now, defer_return=defer_return)
        scenario = self.scenario.get(mission['site_id'])
        if scenario and scenario.get('drone_verifiable'):
            count = scenario['victims']['count']
            assets = [deepcopy(a) for a in self.assets.values() if a['site_id'] == mission['site_id']
                      and a.get('path', '').startswith('assets/drone/')]
            received = max(scan_end, time(mission['last_updated_at']))
            report = {'schema_version':mission['schema_version'],
                'input_id':identity('scenario-scan',[mission['mission_id'],scenario['ground_truth_id']]),
                'timestamp':stamp(received),'drone_id':mission['resource']['resource_id'],
                'location':{**scenario['centroid'],'raw_text':scenario['display_name'], 'resolved_site_id':mission['site_id']},
                'structural_damage':scenario['structural_state'], 'access_status':scenario['access_state'],
                'victim_indicator':{'type':'none' if count == 0 else 'confirmed_individual' if count == 1 else 'confirmed_multiple',
                                    'count_min':count,'count_max':count},
                'metadata':{'provenance':'simulated','simulation':True, 'scenario_id':scenario['scenario_id'],
                    'scenario_ground_truth_id':scenario['ground_truth_id'], 'inspection_source':'scenario_simulated_scan',
                    'assessed_incident_types':['trapped_victims','structural_damage','access_blocked'],
                    'verification_assets':assets, 'mission_id':mission['mission_id']}}
            received_mission = advance_mission(mission, stamp(received), report)
            return advance_mission(received_mission, now, defer_return=defer_return)
        stored = self.stored.get(mission['site_id'])
        if stored:
            report = deepcopy(stored['report'])
            report['input_id'] = identity('database-scan', [mission['mission_id'], stored['report_id']])
            report['drone_id'] = mission['resource']['resource_id']
            report['location']['resolved_site_id'] = mission['site_id']
            received = max(scan_end, time(mission['last_updated_at']))
            report['timestamp'] = stamp(received)
            report.setdefault('metadata', {}).update(database_source_report_id=stored['report_id'],
                inspection_source='stored_simulated_result_replayed_after_scan', mission_id=mission['mission_id'])
            received_mission = advance_mission(mission, stamp(received), report)
            return advance_mission(received_mission, now, defer_return=defer_return)
        matches = [e for e in self.events if e['source_type'] == 'drone'
                   and e['intended_referent']['site_id'] == mission['site_id']]
        if not matches and self.complete_missing:
            received = max(scan_end, time(mission['last_updated_at']))
            point = mission['outbound_route']['path'][-1]
            report = {'schema_version':mission['schema_version'],
                'input_id':identity('scan-unavailable',mission['mission_id']), 'timestamp':stamp(received),
                'drone_id':mission['resource']['resource_id'],
                'location':{**point,'raw_text':mission['site_id'],'resolved_site_id':mission['site_id']},
                'structural_damage':'unknown', 'victim_indicator':{'type':'unknown'}, 'access_status':'unknown',
                'metadata':{'inspection_source':'inspection_unavailable', 'simulation':True,
                            'reason':'No stored or authored inspection exists for this canonical building'}}
            received_mission = advance_mission(mission, stamp(received), report)
            return advance_mission(received_mission, now, defer_return=defer_return)
        if not matches:
            return advance_mission(mission, now, defer_return=defer_return)
        # Each scan consumes one authored event. It never borrows another site's result.
        event = min(matches, key=lambda e: (time(e['raw_input']['timestamp']), e['event_id']))
        ready = max(scan_end, time(event['raw_input']['timestamp']))
        if time(now) < ready:
            return advance_mission(mission, now, defer_return=defer_return)
        report = deepcopy(event['raw_input'])
        validate('drone_observation_input.schema.json', report)
        assets = []
        for asset_id in event.get('revealed_asset_ids', []):
            asset = self.assets[asset_id]
            if (asset['site_id'] != mission['site_id']
                    or event['event_id'] not in asset['reveal_condition']['event_ids']):
                raise ValueError('Verification asset does not belong to this site/event')
            assets.append(deepcopy(asset))
        report['metadata'] = {**report.get('metadata', {}),
            'mission_id': mission['mission_id'], 'scenario_event_id': event['event_id'],
            'scenario_original_input_id': report['input_id'],
            'scenario_original_timestamp': report['timestamp'],
            'scenario_original_drone_id': report['drone_id'],
            'verification_assets': assets}
        report['input_id'] = identity('scan-report', [mission['mission_id'], event['event_id']])
        report['drone_id'] = mission['resource']['resource_id']
        report['location']['resolved_site_id'] = mission['site_id']
        # Capture is at scan completion/event availability; preserve authored provenance.
        # Upgrading an existing pre-provider mission cannot rewind its last tick.
        received = max(ready, time(mission['last_updated_at']))
        report['timestamp'] = stamp(received)
        received_mission = advance_mission(mission, stamp(received), report)
        return advance_mission(received_mission, now, defer_return=defer_return)
