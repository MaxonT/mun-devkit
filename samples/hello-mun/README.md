# MUN Hello

The devkit's native sample: a complete game for the runtime profile
`linux-arm64-gl-v0`, as small as one can be and still do what a game on a
card does.

- Reads its settings and a sound from the card's content.
- Takes the keyboard and a game controller.
- Plays sound.
- Restores the save the console hands it; asks the console to save, on its
  own thread, and says "saved" only when the console has confirmed it.
- Ends with exit code 0 when the player quits and when the console asks it
  to stop.

## Controls

| | Keyboard | Controller |
| --- | --- | --- |
| Change the count | Up, Down | D-pad or left stick |
| Save | Enter, Space or S | South button, or Start |
| Quit | Escape or Q | East button |
| Laboratory: end with exit code 3 | F | |
| Laboratory: abort | C | |

The laboratory keys exist to exercise a console's handling of a failed and
a crashed game; `lab_keys = 0` in `content/hello.cfg` removes them.

## On the screen

The count, with a `*` while it differs from what the card holds; what the
game knows about its save ("NEW GAME", "RESTORED FROM THE CARD", "SAVED ON
THE CARD", "NOT SAVED: no_space"); three lamps for the content, the sound
device and the controller; a bar along the bottom that moves every frame.
One sound plays at start, one on each change, one when a save is confirmed
and another when it is not.

## Build, pack, run

```sh
../../mdk build
../../mdk pack                      # build/hello-mun.img
../../mdk sim --container --env MUN_HELLO_HEADLESS=1 --env "MUN_HELLO_SCRIPT=up up save wait quit"
make test                           # the game's own logic, on this computer
make host && MUN_HELLO_WINDOW=1280x720 build/hello-mun    # a look at it, where SDL2 and OpenAL are installed
```

Outside a session the game runs and says "NOT IN A MUN SESSION": it can be
played, not saved.

## For automated checks

Variables a console never sets:

| Variable | Effect |
| --- | --- |
| `MUN_HELLO_HEADLESS=1` | No window and no sound device |
| `MUN_HELLO_SCRIPT="up up save wait quit"` | Play these steps, one a frame: `up`, `down`, `save`, `wait` (for the save's answer), `idle:N`, `quit`, `fail`, `crash` |
| `MUN_HELLO_CAPTURE=FILE`, `MUN_HELLO_CAPTURE_FRAME=N` | Write frame N (the second by default) as a PPM image |
| `MUN_HELLO_WINDOW=WxH` | A window of that size instead of the whole display |

## Files

| | |
| --- | --- |
| `src/game.c` | The game: a count and its save payload. No SDL |
| `src/main.c` | The session, input, the save thread, the loop |
| `src/draw.c` | Rectangles and a 3×5 typeface on fixed-function OpenGL |
| `src/sound.c` | OpenAL: a WAV from the content, tones made in place |
| `src/config.c` | `hello.cfg` |
| `content/hello.cfg` | Title, colours, step, laboratory keys |
| `mun-project.toml` | What `mdk` builds and what the card says |
