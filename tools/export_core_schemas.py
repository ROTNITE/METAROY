"""Export the active Core schemas for participants and validation tooling."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from contracts import protocol


SCHEMAS={'observation':protocol.OBSERVATION,'decision':protocol.DECISION,
         'reset-response':protocol.RESET_RESPONSE,'step-response':protocol.STEP_RESPONSE}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    args=parser.parse_args()
    args.directory.mkdir(parents=True,exist_ok=False)
    for name,schema in SCHEMAS.items():
        data={'$schema':'https://json-schema.org/draft/2020-12/schema',**schema}
        (args.directory/f'{name}.schema.json').write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':
    main()
