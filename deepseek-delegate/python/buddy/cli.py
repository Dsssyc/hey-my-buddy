"""Structured CLI for the C-Two-backed local service."""
import argparse
import json
from .transport import call_service


def main():
    parser = argparse.ArgumentParser(description="Buddy service: C-Two IPC, dsh delegation and completion delivery")
    parser.add_argument("method", choices=["health","start","status","wait","result","list","cancel","acknowledge","dashboard","stop"])
    parser.add_argument("params", nargs="?", default="{}", help="JSON object")
    args = parser.parse_args()
    try:
        result = call_service(args.method, json.loads(args.params))
        if args.method == 'stop':
            from .receiver import stop_receiver
            stop_receiver()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as error:
        print(json.dumps({"error":{"code":getattr(error,"code","SERVICE_ERROR"),"message":str(error)}}))
        raise SystemExit(1)

if __name__ == "__main__": main()
