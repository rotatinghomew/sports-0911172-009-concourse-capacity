#!/bin/sh
set -eu
temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT
python3 -m unittest discover -s tests -v
python3 -m compileall -q product tests
python3 -m product demo > "$temporary/demo.json"
python3 -c 'import json,sys; result=json.load(open(sys.argv[1])); assert result["state"]; print("业务回放通过")' "$temporary/demo.json"
