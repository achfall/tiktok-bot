#!/usr/bin/env python3
"""CLI layanan panel: state dan konfigurasi tanpa HTTP/browser."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from botapp.service import ControlService
from project_paths import PROJECT_ROOT


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['state','settings','set'])
    parser.add_argument('--values',help='Object JSON konfigurasi')
    parser.add_argument('--account')
    args=parser.parse_args();service=ControlService(PROJECT_ROOT)
    if args.action=='set':print(service.save_settings({'values':json.loads(args.values or '{}'),'account':args.account}))
    else:
        state=service.state()
        print(json.dumps(state if args.action=='state' else {'schema':state['schema'],'values':state['effective']},indent=2,ensure_ascii=False))


if __name__=='__main__':main()
