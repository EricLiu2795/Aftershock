"""Create a clearly simulated labeled handoff without changing the real-map database."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from .store import GraphStore,geojson,version,distance
from .drone import ingest_drone


def build_demo(graph_path,output_dir):
    graph=json.loads(Path(graph_path).read_text())
    if not graph['sites'] or not graph['edges']: raise ValueError('Export needs a site and road')
    output=Path(output_dir); output.mkdir(parents=True,exist_ok=True)
    root=Path(__file__).resolve().parents[1]
    site=graph['sites'][0]
    p=site['site']['centroid']; point=[p['longitude'],p['latitude']]
    edge=min(graph['edges'],key=lambda e:min(distance(point,c) for c in e['path']))
    nodes={n['node_id']:n for n in graph['nodes']}
    when='2026-09-19T14:05:12Z'
    with tempfile.TemporaryDirectory() as tmp:
        store=GraphStore(Path(tmp)/'demo.sqlite')
        try:
            with store.transaction():
                store.metadata('simulation',{'is_simulated':True,'description':'Existing example labels explicitly reassigned to an OSM site for interface demonstration only. Not real earthquake evidence.'})
                store.site(site['site'],site['geometry'])
                for identity in {edge['source'],edge['target']}:
                    pos=nodes[identity]['position']; store.node(identity,[pos['longitude'],pos['latitude']])
                store.edge(edge['edge_id'],edge['road_id'],edge['source'],edge['target'],edge['path'],edge['osm_tags'])
                observations=[]
                for source in ['radio','social']:
                    obs=json.loads((root/f'contracts/examples/structured_{source}_observation.json').read_text())
                    obs['observation_id']='simulation:'+obs['observation_id']
                    obs['location']={'latitude':p['latitude'],'longitude':p['longitude'],'accuracy':'building','resolved_site_id':site['site']['site_id'],'raw_text':None,'label':'SIMULATED site assessment'}
                    obs.setdefault('metadata',{})['simulation']=True
                    observations.append(obs); store.observation(obs)
                drone={'schema_version':version(),'input_id':'simulation:infrared-1','timestamp':when,'drone_id':'simulation:drone-1',
                       'location':observations[0]['location'],'structural_damage':'unknown','access_status':'limited',
                       'victim_indicator':{'type':'possible_multiple','count_min':None,'count_max':None},'metadata':{'simulation':True,'road_assessment':{'road_id':edge['edge_id'],'passability':{'foot':'limited','vehicle':'blocked'},'travel_cost_multiplier':2}}}
                observations.extend(ingest_drone(store,drone))
                road={'schema_version':version(),'assessment_id':'simulation:road-1','road_state':{
                    'schema_version':version(),'road_id':edge['edge_id'],'status':'limited','travel_cost_multiplier':2,
                    'updated_at':when,'evidence_ids':['simulation:evidence-road-1'],'reason':'SIMULATED limited road passage'},
                    
                    'passability':{'foot':'limited','vehicle':'blocked'},'verification_status':'unverified',
                    'evidence':[{'schema_version':version(),'evidence_id':'simulation:evidence-road-1','observation_id':observations[0]['observation_id'],
                                 'source_type':'field_note','summary':'SIMULATED ground-team passage assessment','verification_status':'unverified','timestamp':when}]}
                store.road_assessment(road)
            result=store.export(graph['bbox'])
            artifacts={'labeled_demo.json':result,'labeled_demo.geojson':geojson(result),'site_observations.json':observations,
                       'drone_report.json':drone,'road_assessment.json':road,
                       'route_assessment_foot.json':store.assess_route([edge['edge_id']],at=when),
                       'route_assessment_vehicle.json':store.assess_route([edge['edge_id']],profile='vehicle',at=when)}
            for name,data in artifacts.items():
                (output/name).write_text(json.dumps(data,ensure_ascii=False,allow_nan=False,indent=2)+'\n')
        finally: store.close()
    return {key:str(output/key) for key in artifacts}


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('graph');p.add_argument('output');a=p.parse_args()
    print(json.dumps(build_demo(a.graph,a.output),indent=2))
