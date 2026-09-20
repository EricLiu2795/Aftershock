"""Canonical scan coverage shared by dispatch and human-approved retargeting."""
def inspected_sites(record):
    result = set(record.get('inspected_site_ids', []))
    missions = list(record.get('missions', [])) + [h['mission'] for h in record.get('history', [])]
    for mission in missions:
        if any(e['event_type'] in {'DRONE_VERIFICATION_COMPLETED', 'DRONE_REPORT_READY'} for e in mission.get('events', [])):
            result.add(mission['site_id'])
    return result
