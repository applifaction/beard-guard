#!/bin/bash
SCRIPT_PATH=$(dirname "$0")

cd $SCRIPT_PATH || exit
./venv/bin/python ./beard_guard.py