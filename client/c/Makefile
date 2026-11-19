# Copyright 2026 Iván Moreno Mendoza
# SPDX-License-Identifier: Apache-2.0
# The client's test program (tests/munc.c), built the way a console program
# is: with the devkit's options, warnings as errors. A game compiles mun.c
# itself, with its own build; this is how the devkit shows that the client
# builds clean under those options and carries them.
#
#     make -C client/c BUILD=/some/directory
MUN_DEVKIT ?= ../..
BUILD      ?= build
CFLAGS     ?= -O2 -g0
WARNINGS    = -std=c11 -Wall -Wextra -Wpedantic -Wshadow -Wconversion -Wno-sign-conversion -Werror
HERE        = $(MUN_DEVKIT)/client/c
include $(MUN_DEVKIT)/mk/hardening.mk

$(BUILD)/munc: $(HERE)/tests/munc.c $(HERE)/mun.c $(HERE)/mun.h $(HERE)/Makefile $(MUN_DEVKIT)/mk/hardening.mk
	mkdir -p $(BUILD)
	$(CC) $(WARNINGS) $(CFLAGS) $(MUN_HARDEN_CFLAGS) -I$(HERE) -o $@ $(HERE)/tests/munc.c $(HERE)/mun.c $(MUN_HARDEN_LDFLAGS)

.PHONY: clean
clean:
	rm -f $(BUILD)/munc
