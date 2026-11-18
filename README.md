# MUN Devkit

Tools for making and porting games for MUN™: a host tool (`mdk`), a small
static C client for what a console gives a game, a session simulator, and a
sample that goes from source to a Game Card image.

A MUN game is an ordinary AArch64 Linux program that follows a few contracts
of [MUN OS](https://github.com/Play-MUN/mun-os): a card with a manifest, a
runtime profile, facts in the environment, and saves the console writes to
the card. The devkit does not replace or wrap those contracts. It reuses
MUN OS's own card tool and validator, so a card `mdk` makes is a card the
console's code has already read.

## Status

Early. What exists is what is listed here, and nothing in it has been run on
a console's hardware.

| Part | State |
| --- | --- |
| `mdk doctor`, `os`, `env`, `new`, `build`, `check`, `pack`, `sim` | Work on macOS (Apple Silicon) with a container runtime; Linux expected, not tried |
| MUN OS followed | v0.1.0-dev.5 (`mun-os.lock.json`); the checks also pass with v0.1.0-dev.4's tools |
| Static C client (`client/c`): session facts, restore and acknowledged save | Tested against the simulator and against save files as a console writes them |
| Session simulator | Reproduces the session layout, the save service and how a session ends; not the sandbox or any device |
| Sample `samples/hello-mun` (SDL2, OpenGL, OpenAL; profile `linux-arm64-gl-v0`) | Builds, passes the checks, packs, and runs in the simulator, drawing with Mesa's software renderer |
| Godot 4.7 adapter (`engines/godot`): GDScript, no native code | Saves and restores against the simulator with the pinned engine build; an exported game for a console is not possible yet ([why](engines/godot/README.md)) |
| Other engines | Nothing yet |

The runtime profiles are MUN OS's and are provisional there: its documents
say they are measured in virtual machines only.

## What you need

- Python 3.9 or later.
- MUN OS's own tools, at the revision `mun-os.lock.json` names. `mdk os
  fetch` clones exactly that revision into `.local/mun-os`; `--mun-os` or
  `MUN_OS_DIR` name another checkout, and `../mun-os` beside the devkit is
  used when there is no other. `mdk` reports the revision it finds and says
  when it differs from the pinned one.
- e2fsprogs 1.47 or later, to make card images (macOS: `brew install e2fsprogs`).
- A container runtime that runs `linux/arm64` (`docker`), to build for a
  console. On an ARM64 computer the build runs natively.

`./mdk doctor` checks each of these and says what to do about a missing one.

## From nothing to a saved game

```sh
./mdk doctor                    # what this computer has and lacks
./mdk os fetch                  # once: MUN OS's tools at the pinned revision
./mdk env build                 # once: the build environment (needs the network)

./mdk new star-well --title "Star Well" --id org.example.star-well
cd star-well
../mdk build                    # compile inside the environment
../mdk check                    # the build against its runtime profile
../mdk pack                     # build/star-well.img, and a record of what is in it
../mdk sim --container --fresh --env MUN_HELLO_HEADLESS=1 \
           --env "MUN_HELLO_SCRIPT=up up up save wait quit"     # a session that saves
../mdk sim --container --env MUN_HELLO_HEADLESS=1 \
           --env "MUN_HELLO_SCRIPT=up save wait quit"           # the next one restores
```

`mdk new` copies the native sample under the project's name; from then on
the project is its own. `tests/test_journey.py` runs exactly these steps and
requires each result. `make sample` at the top builds and packs the sample
itself; `make check` runs the tests this computer can run and names the ones
it skipped.

To see the card in MUN OS's virtual console, from the MUN OS checkout
(`.local/mun-os` after `mdk os fetch`): `./mun get`, `./mun dev run
--window`, then `./mun dev vm dev card-attach NAME` with the image copied to
its `.local/gamecards/NAME.img` (MUN OS's `docs/getting-started.md`).

### The build environment

`mdk build` runs the project's build command in a Debian 13 ARM64 container
whose packages come from the same dated Debian snapshot as MUN OS's image
(`tools/env/Dockerfile`), without network access. The program therefore
links against the library versions a console of that MUN OS line has, and
the build does not depend on what the host has installed. `mdk env info`
prints the compiler and library versions.

### The checks

`mdk check` reads the entry and the content before a card is made:

```
E-ARCH    game is a 64-bit little-endian x86-64 program. MUN runs 64-bit AArch64 Linux programs.
          Build or export it for Linux ARM64 (aarch64); a Windows, macOS or x86-64 build must be made again.

E-LIB     game needs libcurl.so.4, which the profile linux-arm64-gl-v0 does not provide.
          The profile provides: libEGL.so.1, libGL.so.1, libSDL2-2.0.so.0, ...
          Link it into the game statically, or drop the feature that uses it. The card's content is
          read-only data: a library placed there cannot be loaded.
```

Each finding has a stable code, what is wrong and what to do. The codes:
`E-ENTRY`, `E-NOT-ELF`, `E-ARCH`, `E-NOT-PROGRAM`, `E-SIZE`, `E-INTERP`,
`E-LIB`, `E-GLIBC`, `E-CONTENT`, `E-CONTENT-LINK`, `E-CONTENT-ENTRY`,
`E-CARD-ID`, `E-CARD-VERSION`, `E-CARD-TITLE`, `E-PROFILE`, and the warnings
`W-RUNPATH`, `W-CODE`, `W-CARD-SIZE`. The card itself is validated by
MUN OS's tool, whose own codes are in MUN OS's `docs/game-cards.md`.

### The card

`mdk pack` runs MUN OS's `mun-card create` and then `mun-card inspect`, and
writes `build/<name>.card.json`: the image's SHA-256, what a console reads
from it, the entry's hash and libraries, and the tool revisions. The same
inputs and the same e2fsprogs give the same image, byte for byte.

### The simulator

`mdk sim` runs the game as a session: a copy of the entry started with no
arguments, `work/` as its home, the `MUN_*` facts, the save restored from a
card directory (`build/sim-card`), and a save socket that answers as the
console's does, with its codes and sentences.

```sh
mdk sim --container --fail no_space      # every save is refused as a full card
mdk sim --container --stop-after 5       # SIGTERM after 5 s, SIGKILL 3 s later
mdk sim --container --save-delay 2       # a slow card
mdk sim --fresh                          # start from a card with no save
```

It does not reproduce the sandbox (user, devices, read-only system,
non-executable content, memory and task limits), the display, sound or
input devices, or a real card. A game that passes in it follows the
contracts; whether it runs on a console is established on a console.

## Saves from C

```c
#include "mun.h"

mun_session *session;
mun_open(&session);

static char payload[MUN_SAVE_LOAD_MAX_BYTES];
size_t length;
mun_save_info info;
switch (mun_save_load(session, payload, sizeof payload, &length, &info)) {
case MUN_OK:      /* parse `payload`, a JSON object, by info.schema */ break;
case MUN_NO_SAVE: /* a new game */ break;
default:          /* unavailable or damaged: start without it */ break;
}

mun_save_result result;
if (mun_save_write(session, 1, "{\"level\":3}", 11, 25000, &result) == MUN_OK)
    show_saved();                       /* only now: the card holds it */
else
    show_not_saved(result.code);        /* "no_space", "card_removed", ... */
```

`mun_save_write` blocks until the console answers: call it off the thread
that draws. `client/c/mun.h` documents every function, status and rule.
Compile `mun.c` into the game; there is no shared library.

## Layout

```
mdk                    the host tool's entry point
tools/mdk/             its source (Python, standard library only)
tools/env/             the build environment's definition
client/c/              mun.h, mun.c and the client's test program
contracts/profiles/    what mdk checks a build against, per runtime profile
samples/hello-mun/     the native sample
samples/hello-mun-godot/   the same small game as a Godot project
engines/godot/         the Godot adapter, and how to fetch the pinned engine
tests/                 host tests; the sample's run in the environment
mun-os.lock.json       the MUN OS revision the devkit was checked against
```

## Licence

No licence has been chosen for this repository yet.
[docs/third-party.md](docs/third-party.md) lists what it uses and under
which terms.
