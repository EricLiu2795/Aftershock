"""Explicit demo scenario inputs, joined by canonical identity after report import.

The interactive tracking demo is authorized to use simulator counts for ranking.
This adapter never seeds reports or supplies data to normal coordinator planning.
"""
import json
from pathlib import Path

PACK = Path(__file__).resolve().parents[1] / 'data/scenarios/caraballeda'


def scenario_buildings(pack=PACK):
    pack = Path(pack)
    active = json.loads((pack / 'active_sites.json').read_text())['active_worksites']
    truth = json.loads((pack / 'scenario_ground_truth.json').read_text())
    by_id = {entry['site']['site_id']: entry for entry in active}
    result = {}
    for entry in truth['worksites']:
        sid = entry['site_id']
        site = by_id.get(sid)
        if site is None or site['worksite_key'] != entry['worksite_key']:
            raise ValueError('Scenario building has no matching canonical active Site')
        if sid in result:
            raise ValueError('Duplicate scenario building identity')
        count = entry['victims']['count']
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError('Scenario people count must be a nonnegative integer')
        result[sid] = {**entry, 'display_name': site['display_name'],
                      'scenario_id': truth['scenario_id'], 'centroid': site['site']['centroid']}
    return result


def buildings_from_rows(rows):
    """Same shape as scenario_buildings(), built from the API's SQLite `scenario_buildings` table.

    The database is the source of per-building people counts for the interactive demo.
    Counts are simulated scenario inputs, not verified casualties.
    """
    result = {}
    for row in rows:
        sid = row['site_id']
        if sid in result:
            raise ValueError('Duplicate scenario building identity')
        count = row['people_count']
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError('Scenario people count must be a nonnegative integer')
        result[sid] = {
            'ground_truth_id': row['ground_truth_id'], 'worksite_key': row['worksite_key'], 'site_id': sid,
            'simulated': True, 'display_name': row['display_name'], 'scenario_id': row['pack_scenario_id'],
            'centroid': {'latitude': row['latitude'], 'longitude': row['longitude']},
            'victims': {'trapped': count > 0, 'count': count, 'count_min': count, 'count_max': count,
                        'indication': 'none' if count == 0 else 'confirmed_individual' if count == 1 else 'confirmed_multiple'},
            'structural_state': row['structural_state'], 'access_state': row['access_state'],
            'drone_verifiable': bool(row['drone_verifiable']), 'photo_asset_id': row.get('photo_asset_id')}
    return result
