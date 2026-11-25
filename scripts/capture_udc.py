"""Phone UDC task entry; no LCD device, pupil scan or lab RawCapture conversion."""
import argparse
import json
from pathlib import Path
import sys

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tasks.udc.plan import UDCCapturePlan


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--validate-only", action="store_true", help="Validate/print the plan; no devices or output files")
    mode.add_argument("--hardware", action="store_true")
    parser.add_argument("--adb", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--condition-verified", action="store_true", help="Operator confirms the current physical arrangement")
    parser.add_argument("--camera-id")
    parser.add_argument("--serial", help="Local transport selection, not persisted")
    args = parser.parse_args(argv)
    if args.hardware and (args.adb is None or args.output is None):
        parser.error("--hardware requires explicit --adb and a new --output directory")
    try:
        plan = UDCCapturePlan.load_json(args.plan)
        if args.validate_only:
            print(json.dumps(plan.to_dict(), indent=2, allow_nan=False))
            return 0
        from tasks.udc.capture import run_udc_capture_plan
        result = run_udc_capture_plan(plan, adb=args.adb, output=args.output,
                                      condition_verified=args.condition_verified,camera_id=args.camera_id,serial=args.serial)
        print(json.dumps({"output":str(args.output.resolve()),"capture_complete":result["capture_complete"],"run_succeeded":result["run_succeeded"]}))
        return 0
    except (ValueError, RuntimeError, OSError) as error:
        print(f"UDC task failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
