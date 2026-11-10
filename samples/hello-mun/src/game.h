/* MUN Hello's whole game: a count the player raises and lowers, and how many
 * times it has been saved. No SDL, no console: plain C that the host tests
 * run as it is. */
#ifndef HELLO_GAME_H
#define HELLO_GAME_H

#include <stddef.h>
#include <stdint.h>

#define HELLO_SAVE_SCHEMA 1u /* bump when the payload's shape changes */
#define HELLO_COUNT_MAX 9999

struct game {
    int64_t count;
    int64_t saves; /* saves the console has confirmed, over the game's whole life */
    int dirty;     /* the count changed since the last confirmed save */
};

void game_init(struct game *game);

/* Adds `delta`, keeping the count within 0..HELLO_COUNT_MAX. Returns 1 if it changed. */
int game_change(struct game *game, int delta);

/* The payload to save for this state, as it will be once the console
 * confirms it (`saves` already counts this one). Returns the length written,
 * without the terminator, or -1 if `capacity` is too small. */
int game_payload(const struct game *game, char *out, size_t capacity);

/* Applies a restored payload of `schema`. Returns 0 on success; -1 if the
 * schema is one this version cannot read or the payload does not hold what
 * that schema promises. The game is unchanged on failure. */
int game_restore(struct game *game, const char *payload, size_t length, uint32_t schema);

/* Called when the console has confirmed the save of `payload`. */
void game_saved(struct game *game, const char *payload, size_t length);

#endif
