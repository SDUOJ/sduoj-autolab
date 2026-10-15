#!/usr/bin/env python3
"""Audit and convert a local, already-exported Problem Stack Prisma JSON file."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from auto_task.problem_stack_migration import ConversionError, convert_export, write_bundle


def load_object(path):
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ConversionError("Expected a JSON object: " + str(path))
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Local sanitized Prisma table export")
    parser.add_argument("--identity-map", required=True, type=Path,
                        help="Legacy User.id to verified SDUOJ userId/username JSON")
    parser.add_argument("--course-map", required=True, type=Path,
                        help="Legacy resource source ID to course_id or psid JSON")
    parser.add_argument("--asset-source", type=Path, help="Only directory from which referenced assets may be read")
    parser.add_argument("--output", type=Path, help="Neutral JSON bundle path; written only with --write")
    parser.add_argument("--asset-output", type=Path, help="Neutral asset directory; written only with --write")
    parser.add_argument("--write", action="store_true", help="Write the reviewed bundle and copy verified assets")
    args = parser.parse_args(argv)
    try:
        bundle = convert_export(load_object(args.input), load_object(args.identity_map),
                                load_object(args.course_map), args.asset_source)
        result = {"dry_run": not args.write, "report": bundle["report"],
                  "resource_ids": [item["id"] for item in bundle["resources"]],
                  "asset_ids": [item["id"] for item in bundle["assets"]]}
        if args.write:
            if not args.output:
                raise ConversionError("--output is required with --write")
            result["written"] = write_bundle(bundle, args.output, args.asset_source, args.asset_output)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0 if bundle["report"]["ready_to_import"] else 2
    except (ConversionError, OSError, json.JSONDecodeError) as error:
        print("error: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
