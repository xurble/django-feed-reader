#!/bin/bash

ruff check --select I,F401 --fix --force-exclude .
r=$?
ruff format --force-exclude .

bandit -r feeds
b=$?
[ $r -ne 0 ] && exit $r
exit $b

