# Copyright 2026 Iván Moreno Mendoza
# SPDX-License-Identifier: Apache-2.0
# MUN Devkit. `make check` runs what this computer can run and says what it
# skipped; `make check-full` fails on what it cannot run; `make sample`
# builds and packs the native sample.
PYTHON ?= python3
MDK     = $(PYTHON) mdk

check:
	$(PYTHON) -m unittest discover -s tests -p 'test_*.py' -v
	$(MAKE) -C samples/hello-mun test BUILD=build/host

# The same tests as a gate: one that cannot run here fails instead of being
# skipped. Needs the SDK environment, the sample's build and the engine
# (`make env sample`, `engines/godot/fetch.sh`).
check-full:
	MDK_REQUIRE_ALL=1 $(PYTHON) -m unittest discover -s tests -p 'test_*.py'
	$(MAKE) -C samples/hello-mun test BUILD=build/host

sample:
	$(MDK) build --project samples/hello-mun
	$(MDK) pack --project samples/hello-mun

env:
	$(MDK) env build

doctor:
	$(MDK) doctor

clean:
	$(MAKE) -C samples/hello-mun clean

.PHONY: check check-full sample env doctor clean
