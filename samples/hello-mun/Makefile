# Copyright 2026 Iván Moreno Mendoza
# SPDX-License-Identifier: MIT-0
# MUN Hello. Built by `mdk build` inside the SDK environment, which has the
# console's own library versions; `make host` builds it for the developer's
# own computer where SDL2 and OpenAL exist, for a look at it in a window.
MUN_DEVKIT ?= ../..
NAME       ?= hello-mun
BUILD      ?= build
CC         ?= gcc
CFLAGS     ?= -O2 -g0
WARNINGS    = -std=c11 -Wall -Wextra -Wpedantic -Wshadow -Wconversion -Wno-sign-conversion
SRC         = src/main.c src/game.c src/draw.c src/sound.c src/config.c $(MUN_DEVKIT)/client/c/mun.c
HDR         = src/game.h src/draw.h src/sound.h src/config.h $(MUN_DEVKIT)/client/c/mun.h
SDL_CFLAGS ?= $(shell sdl2-config --cflags)
SDL_LIBS   ?= $(shell sdl2-config --libs)
ifeq ($(shell uname -s),Darwin)
AL_LIBS    ?= -framework OpenAL
else
AL_LIBS    ?= -lopenal
endif

all: $(BUILD)/$(NAME) content

# -D_GNU_SOURCE is not needed: the sources use C11, POSIX and the two libraries.
$(BUILD)/$(NAME): $(SRC) $(HDR)
	mkdir -p $(BUILD)
	$(CC) $(WARNINGS) $(CFLAGS) -I$(MUN_DEVKIT)/client/c $(SDL_CFLAGS) -o $@ $(SRC) $(SDL_LIBS) $(AL_LIBS) -lm

# The card's content: the configuration and a sound, made by a small script so
# that no binary file is kept in the repository.
content: $(BUILD)/content/hello.cfg $(BUILD)/content/sounds/blip.wav

$(BUILD)/content/hello.cfg: content/hello.cfg
	mkdir -p $(BUILD)/content
	cp content/hello.cfg $@

$(BUILD)/content/sounds/blip.wav: tools/make_blip.py
	mkdir -p $(BUILD)/content/sounds
	python3 tools/make_blip.py $@

host: all

test: src/game.c src/config.c tests/test_game.c $(MUN_DEVKIT)/client/c/mun.c
	mkdir -p $(BUILD)
	$(CC) $(WARNINGS) -I$(MUN_DEVKIT)/client/c -Isrc -o $(BUILD)/test_game tests/test_game.c src/game.c src/config.c $(MUN_DEVKIT)/client/c/mun.c
	$(BUILD)/test_game

clean:
	rm -rf $(BUILD)

.PHONY: all content host test clean
