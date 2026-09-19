"""Graph topology, evidence preservation, schema compatibility and route assessment."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from geograph import GraphStore,normalize_drone
from geograph.drone import ingest_drone
from geograph.osm_import import import_osm
from geograph.store import geojson,validate,version

ROOT=Path(__file__).resolve().parents[1]
TIME='2026-09-19T14:05:12Z'


def assessment(identity='a',status='blocked',timestamp=TIME):
    return {'schema_version':version(),'assessment_id':identity,
            'road_state':{'schema_version':version(),'road_id':'r1','status':status,'travel_cost_multiplier':2,
                          'evidence_ids':['ev1'],'updated_at':timestamp},
            'passability':{'foot':status,'vehicle':status},'verification_status':'unverified',
            'evidence':[{'schema_version':version(),'evidence_id':'ev1','observation_id':'obs1','source_type':'drone',
                         'summary':'Structured report','verification_status':'unverified','timestamp':timestamp}]}


class GraphTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.store=GraphStore(Path(self.temp.name)/'test.sqlite')
        self.report=json.loads((ROOT/'contracts/examples/drone_observation.json').read_text())
        self.site=json.loads((ROOT/'contracts/examples/site_d3.json').read_text())
        with self.store.transaction():
            self.store.site(self.site)
            for i in range(3): self.store.node(str(i),[-66.85+i*.001,10.61])
            self.store.edge('e1','r1','0','1',[[-66.85,10.61],[-66.849,10.61]],{'highway':'residential'})
            self.store.edge('e2','r2','1','2',[[-66.849,10.61],[-66.848,10.61]],{'highway':'residential'})

    def tearDown(self): self.store.close(); self.temp.cleanup()

    def test_drone_schema_and_multiple_incidents(self):
        report=deepcopy(self.report); report['victim_indicator']={'type':'possible_multiple'}
        records=normalize_drone(report)
        self.assertEqual({r['incident_type'] for r in records},{'structural_damage','access_blocked','trapped_victims'})
        for r in records:
            validate('observation.schema.json',r)
            self.assertEqual(r['verification_status'],'unverified')
            self.assertEqual(r['metadata']['sensor_type'],'infrared')

    def test_unknown_drone_makes_no_invented_incident(self):
        report=deepcopy(self.report)
        for key in ['structural_damage','access_status','victim_indicator']: report.pop(key,None)
        self.assertEqual(normalize_drone(report),[])

    def test_idempotence_and_conflicting_input(self):
        ingest_drone(self.store,self.report,self.site['site_id'])
        count=self.store.stats()['records']
        ingest_drone(self.store,self.report,self.site['site_id'])
        self.assertEqual(count,self.store.stats()['records'])
        changed=deepcopy(self.report); changed['access_status']='open'
        with self.assertRaises(ValueError): ingest_drone(self.store,changed,self.site['site_id'])
        self.assertEqual(count,self.store.stats()['records'])

    def test_unresolved_report_then_explicit_resolution(self):
        obs=json.loads((ROOT/'contracts/examples/structured_social_observation.json').read_text())
        with self.store.transaction(): self.store.observation(obs)
        self.assertEqual(self.store.labels(self.site['site_id'])['observations'],[])
        with self.store.transaction(): self.store.observation(obs,self.site['site_id'])
        self.assertEqual(self.store.labels(self.site['site_id'])['observations'],[obs])

    def test_unknown_road_not_open_and_stale_not_open(self):
        self.assertEqual(self.store.assess_route(['e1'],at=TIME)['status'],None)
        with self.store.transaction(): self.store.road_assessment(assessment(status='open'))
        self.assertEqual(self.store.assess_route(['e1'],at=TIME)['status'],'open')
        self.assertEqual(self.store.assess_route(['e1'],at='2026-09-20T14:05:12Z')['status'],None)

    def test_blocked_profile_difference_and_conflict(self):
        a=assessment(); a['passability']['foot']='limited'
        with self.store.transaction(): self.store.road_assessment(a)
        self.assertEqual(self.store.assess_route(['e1'],at=TIME)['status'],'limited')
        self.assertEqual(self.store.assess_route(['e1'],profile='vehicle',at=TIME)['status'],'blocked')
        with self.store.transaction(): self.store.road_assessment(assessment('b','open'))
        self.assertEqual(self.store.assess_route(['e1'],at=TIME)['status'],None)

    def test_out_of_order_assessments(self):
        with self.store.transaction():
            self.store.road_assessment(assessment('new','blocked'))
            self.store.road_assessment(assessment('old','open','2026-09-19T13:05:12Z'))
        self.assertEqual(self.store.assess_route(['e1'],at=TIME)['status'],'blocked')

    def test_atomic_batch_and_evidence_validation(self):
        with self.assertRaises(ValueError):
            with self.store.transaction():
                ingest_drone(self.store,self.report,self.site['site_id'])
                bad=assessment(); bad['road_state']['evidence_ids']=['missing']
                self.store.road_assessment(bad)
        self.assertEqual(self.store.stats()['records'],0)

    def test_direction_and_disconnection(self):
        with self.store.transaction():
            self.store.node('3',[-66.847,10.61])
            self.store.edge('e3','r3','3','2',[[-66.847,10.61],[-66.848,10.61]],{'highway':'residential','oneway':'yes'})
        with self.assertRaises(ValueError): self.store.assess_route(['e1','e2','e3'],profile='vehicle',at=TIME)
        self.assertEqual(self.store.assess_route(['e1','e2','e3'],at=TIME)['status'],None)
        with self.assertRaises(ValueError): self.store.assess_route(['e1','e3'],at=TIME)

    def test_drone_road_metadata_and_canonical_state(self):
        report=deepcopy(self.report)
        report['metadata']['road_assessment']={'road_id':'e1',
                'passability':{'foot':'limited','vehicle':'blocked'}}
        ingest_drone(self.store,report)
        self.assertEqual(self.store.assess_route(['e1'],at=TIME)['status'],'limited')
        self.assertEqual(self.store.assess_route(['e1'],profile='vehicle',at=TIME)['status'],'blocked')
        state=deepcopy(assessment()['road_state']);state['road_id']='e2'
        with self.store.transaction(): self.store.road_state(state)
        graph=self.store.export([-66.9,10.6,-66.8,10.62])
        self.assertEqual(graph['edges'][1]['labels']['reported_status'],'blocked')
        self.assertEqual(self.store.assess_route(['e2'],at=TIME)['status'],None)

    def test_only_three_road_conditions(self):
        from jsonschema import ValidationError
        for state in ['blocked','limited','open']:
            validate('road_assessment.schema.json',assessment(status=state))
        bad=assessment(); bad['conditions']=[{'code':'debris'}]
        with self.assertRaises(ValidationError): validate('road_assessment.schema.json',bad)
        bad=assessment(); bad['passability']['foot']='flooding'
        with self.assertRaises(ValidationError): validate('road_assessment.schema.json',bad)

    def test_drone_road_unknown_identity_rolls_back(self):
        report=deepcopy(self.report)
        report['metadata']['road_assessment']={'road_id':'absent',
                'passability':{'foot':'blocked','vehicle':'blocked'}}
        with self.assertRaises(ValueError): ingest_drone(self.store,report)
        self.assertEqual(self.store.stats()['records'],0)

    def test_geojson_and_graph_contract(self):
        with self.store.transaction(): self.store.road_assessment(assessment())
        graph=self.store.export([-66.9,10.6,-66.8,10.62])
        validate('labeled_graph.schema.json',graph)
        self.assertEqual(len(graph['nodes']),3)
        self.assertEqual(graph['sites'],[]) # Los Angeles fixtures must not label Venezuela.
        self.assertEqual(geojson(graph)['features'][0]['geometry']['coordinates'][0],[-66.85,10.61])
        self.store.close(); self.store=GraphStore(Path(self.temp.name)/'test.sqlite')
        self.assertEqual(self.store.stats()['records'],1)


class OSMTests(unittest.TestCase):
    def test_junction_simplification_and_building_import(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'tiny.osm'
            path.write_text('''<osm version="0.6" generator="test">
              <node id="1" lat="10" lon="-66"/><node id="2" lat="10" lon="-65.999"/>
              <node id="3" lat="10" lon="-65.998"/><node id="4" lat="10.001" lon="-65.998"/>
              <node id="5" lat="10.002" lon="-65.998"/>
              <node id="6" lat="10.001" lon="-66"/><node id="7" lat="10.001" lon="-65.999"/>
              <node id="8" lat="10.002" lon="-65.999"/>
              <way id="10"><nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/><tag k="highway" v="residential"/></way>
              <way id="11"><nd ref="3"/><nd ref="5"/><tag k="highway" v="footway"/></way>
              <way id="12"><nd ref="6"/><nd ref="7"/><nd ref="8"/><nd ref="6"/><tag k="building" v="yes"/></way>
              </osm>''')
            db=Path(temp)/'graph.sqlite'; stats=import_osm(path,db)
            self.assertEqual(stats['edges'],3); self.assertEqual(stats['sites'],1)
            store=GraphStore(db)
            try:
                graph=store.export([-67,9,-65,11])
                self.assertNotIn('osm:node:2',{n['node_id'] for n in graph['nodes']})
                self.assertEqual(len(graph['edges'][0]['path']),3)
                self.assertEqual(graph['sites'][0]['site']['site_id'],'osm:way:12')
                self.assertTrue(all(not c['routable'] for c in graph['access_connections']))
            finally: store.close()
            with self.assertRaises(FileExistsError): import_osm(path,db)
