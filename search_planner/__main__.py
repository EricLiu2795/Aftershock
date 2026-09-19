"""File-based integration: plan, dispatch, tick, apply. Never uses wall-clock time."""
import argparse
import json
import os
from pathlib import Path
import tempfile
from .planner import SearchConfig,plan_search
from .runtime import start_mission,advance_mission,apply_missions


def read(path):
    return json.loads(Path(path).read_text())


def save(path,payload):
    destination=Path(path);destination.parent.mkdir(parents=True,exist_ok=True)
    text=json.dumps(payload,sort_keys=True,indent=2,allow_nan=False)+'\n'
    with tempfile.NamedTemporaryFile(mode='w',dir=destination.parent,delete=False,suffix='.tmp') as handle:
        temporary=Path(handle.name);handle.write(text);handle.flush();os.fsync(handle.fileno())
    try:os.replace(temporary,destination)
    finally:
        if temporary.exists():temporary.unlink()


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    plan=sub.add_parser('plan');plan.add_argument('state');plan.add_argument('output');plan.add_argument('--config')
    dispatch=sub.add_parser('dispatch');dispatch.add_argument('state');dispatch.add_argument('plan');dispatch.add_argument('drone_id');dispatch.add_argument('output')
    tick=sub.add_parser('tick');tick.add_argument('mission');tick.add_argument('output');tick.add_argument('--at',required=True);tick.add_argument('--report')
    apply=sub.add_parser('apply');apply.add_argument('state');apply.add_argument('output');apply.add_argument('--missions',nargs='+',required=True);apply.add_argument('--state-version',required=True)
    a=parser.parse_args()
    if a.command=='plan':result=plan_search(read(a.state),SearchConfig(**read(a.config)) if a.config else None)
    elif a.command=='dispatch':result=start_mission(read(a.plan),a.drone_id,read(a.state))
    elif a.command=='tick':result=advance_mission(read(a.mission),a.at,read(a.report) if a.report else None)
    else:result=apply_missions(read(a.state),[read(path) for path in a.missions],a.state_version)
    save(a.output,result)
    print(a.output)


if __name__=='__main__':main()
