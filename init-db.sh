#!/usr/bin/env bash

# Database working directories, plus ./code which is bind-mounted into the DA
# and Agg for custom code (see CUSTOM_CODE.md).
mkdir -p data/db data/rt data/logs data/imports code

# Sample data files -> the import drop-zone.
cp samples/data/* data/imports/

# Sample custom-code (UDA) examples -> ./code. -n never clobbers files you have
# already edited (e.g. when reset-db.sh re-runs this).
cp -rn samples/code/. code/ 2>/dev/null || true

chmod -R 777 data
