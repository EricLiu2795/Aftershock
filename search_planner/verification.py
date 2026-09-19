"""Reveal authored demo drone JSON and media only after a site's scan completes.

Only scenario_events/verification_assets are read. Hidden simulator ground truth
and downstream interpretation hints are never used to generate findings.
"""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
from .common import identity, index, stamp, time, validate
from .runtime import advance_mission

PACK = Path(__file__).resolve().parents[1] / 'data/scenarios/caraballeda'


class DemoVerification:
    def __init__(self, pack=PACK):
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
        matches = [e for e in self.events if e['source_type'] == 'drone'
                   and e['intended_referent']['site_id'] == mission['site_id']]
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
