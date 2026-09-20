from copy import deepcopy
import unittest
from search_planner.road_scan import in_range, scan_roads
from search_planner.common import validate

T='2026-09-19T14:00:00Z'
def point(lon,lat=10.6):return {'latitude':lat,'longitude':lon}
def record(now, start=T, duration=120):
    m={'mission_id':'m1','resource':{'resource_id':'drone-1'},'last_updated_at':now,
       'outbound_route':{'path':[point(-66.85),point(-66.83)],'created_at':start,'estimated_travel_time_s':duration},'return_route':None}
    return {'missions':[m],'state':{'scenario_time':now,'state_version':'test','road_states':[]}}
ROAD={'R1':[[-66.84,10.599],[-66.84,10.601]]}
FIX={'simulation':True,'baseline':{'effective_at':T,'status':'open','passability':{'foot':'open','vehicle':'open'},'travel_cost_multiplier':1},
     'changes':[{'id':'R1','road_id':'R1','effective_at':'2026-09-19T14:01:00Z','status':'blocked','passability':{'foot':'limited','vehicle':'blocked'},'travel_cost_multiplier':1}]}

class RoadScanTests(unittest.TestCase):
    def test_swept_path_detects_road_when_both_endpoints_are_far(self):
        result=scan_roads(record(T),record('2026-09-19T14:02:00Z'),roads=ROAD,fixture=FIX)
        self.assertEqual(len(result['road_scans']),2)
        self.assertEqual(result['road_state_updates'][0]['status'],'blocked')
        self.assertEqual(result['state']['road_states'],[])
        self.assertEqual(result['state']['state_version'],'test')
        for d in result['road_scans']:validate('road_assessment.schema.json',d['assessment'])
        again=scan_roads(record(T),result,roads=ROAD,fixture=FIX)
        self.assertEqual(again,result)

    def test_real_r1_fixture_is_detected_after_activation(self):
        import json
        from search_planner.road_scan import ROOT
        features=json.loads((ROOT/'data/graph_exports/caraballeda.geojson').read_text())['features']
        edge=next(f for f in features if f['id']=='osm:way:376529129:segment:1-12')
        a=record('2026-09-19T14:04:00Z',start='2026-09-19T14:04:00Z',duration=10)
        b=record('2026-09-19T14:04:10Z',start='2026-09-19T14:04:00Z',duration=10)
        path=[{'longitude':p[0],'latitude':p[1]} for p in edge['geometry']['coordinates'][:2]]
        a['missions'][0]['outbound_route']['path']=path
        b['missions'][0]['outbound_route']['path']=path
        result=scan_roads(a,b)
        detection=next(d for d in result['road_scans'] if d['disruption_id']=='R1')
        self.assertEqual(detection['assessment']['road_state']['status'],'blocked')
        self.assertEqual(detection['assessment']['passability'],{'foot':'limited','vehicle':'blocked'})
        self.assertEqual(detection['source_event_id'],'E10')

    def test_radius_near_and_far(self):
        self.assertTrue(in_range(point(-66.85),point(-66.83),[[-66.84,10.6044],[-66.839,10.6044]]))
        self.assertFalse(in_range(point(-66.85),point(-66.83),[[-66.84,10.6046],[-66.839,10.6046]]))

    def test_no_future_or_stationary_detection(self):
        early=scan_roads(record(T),record('2026-09-19T14:00:50Z'),roads=ROAD,fixture=FIX)
        self.assertTrue(early['road_scans'])
        self.assertTrue(all(d['assessment']['road_state']['status']=='open' for d in early['road_scans']))
        late=scan_roads(record('2026-09-19T14:03:00Z'),record('2026-09-19T14:04:00Z'),roads=ROAD,fixture=FIX)
        self.assertEqual(late['road_scans'],[])
        # Incident activates after drone passed: a delayed tick must not reveal it.
        fix=deepcopy(FIX);fix['changes'][0]['effective_at']='2026-09-19T14:03:00Z'
        passed=scan_roads(record(T),record('2026-09-19T14:04:00Z'),roads=ROAD,fixture=fix)
        self.assertEqual(passed['road_state_updates'],[])
        self.assertTrue(all(d['assessment']['road_state']['status']=='open' for d in passed['road_scans']))

    def test_limited_return_flight_and_unknown_road(self):
        fix=deepcopy(FIX);fix['changes']=[];fix['baseline']['status']='limited'
        a=record('2026-09-19T14:03:00Z');b=record('2026-09-19T14:05:00Z')
        b['missions'][0]['return_route']={'path':[point(-66.83),point(-66.85)],'created_at':'2026-09-19T14:03:00Z','estimated_travel_time_s':120}
        result=scan_roads(a,b,roads=ROAD,fixture=fix)
        self.assertEqual(result['road_state_updates'][0]['status'],'limited')
        with self.assertRaises(ValueError):scan_roads(a,b,roads={},fixture=FIX)

    def test_small_and_large_ticks_observe_same_condition_versions(self):
        whole=scan_roads(record(T),record('2026-09-19T14:02:00Z'),roads=ROAD,fixture=FIX)
        result=record(T)
        for second in range(10,121,10):
            at=f'2026-09-19T14:{second//60:02}:{second%60:02}Z'
            nxt=record(at);nxt['road_scans']=result.get('road_scans',[]);nxt['state']['road_states']=result['state']['road_states']
            result=scan_roads(result,nxt,roads=ROAD,fixture=FIX)
        self.assertEqual({d['detection_id'] for d in result['road_scans']},{d['detection_id'] for d in whole['road_scans']})
        self.assertEqual(result['road_state_updates'][0]['status'],'blocked')

if __name__=='__main__':unittest.main()
