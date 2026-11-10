# MUN Devkit. `make check` runs what this computer can run and says what it
# skipped; `make sample` builds and packs the native sample.
PYTHON ?= python3
MDK     = $(PYTHON) mdk

check:
	$(PYTHON) -m unittest discover -s tests -p 'test_*.py' -v
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

.PHONY: check sample env doctor clean
