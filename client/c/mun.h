/* mun.h — static client for the contracts MUN OS gives a game session.
 *
 * It covers what a console implements today: the session's facts (environment
 * variables) and single-object saves on the Game Card (docs/runtime.md and
 * docs/saves.md in MUN OS). Compile mun.c into the game; there is no shared
 * library and no ABI promise beyond this source. The compatibility surface is
 * the console's wire contract, which this file follows.
 *
 * Rules that hold for every function:
 *   - No threads are created, no signal handlers installed, no callbacks made,
 *     no global state kept.
 *   - The only allocation that crosses the boundary is the session object:
 *     mun_open() allocates it with malloc(), mun_close() frees it.
 *   - Buffers are the caller's, with explicit capacities; nothing is retained
 *     after a call returns.
 *   - A mun_session may be used from one thread at a time. Different sessions
 *     are independent.
 *   - mun_save_write() blocks until the console answers or the timeout ends;
 *     call it off the thread that draws frames. Everything else is local and
 *     quick (file and memory only).
 *   - Outside a MUN session (an editor, a desktop build) the variables are
 *     absent and the save functions return MUN_UNAVAILABLE; nothing fails.
 */
#ifndef MUN_H
#define MUN_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Version of this source: major << 16 | minor << 8 | patch. */
#define MUN_CLIENT_VERSION 0x000200u

/* The console's bound on a save's payload, in bytes (docs/saves.md). The
 * console measures the payload as compact JSON in UTF-8: no space between
 * values, every character as itself. This client refuses a text longer than
 * the bound without asking, so a payload written with spaces or escapes is
 * refused here a little earlier than a console would. (The previews before
 * MUN OS v0.1.0-dev.5 counted every character outside ASCII as six bytes; a
 * payload under this bound could be refused there with "save_too_large".) */
#define MUN_SAVE_MAX_BYTES 65536u

/* A buffer of this size always holds a restored payload with its
 * terminator. It is larger than MUN_SAVE_MAX_BYTES because the console keeps
 * the payload as a JSON value and writes it again with a space after each
 * separator: what comes back is the same value, not necessarily the same
 * bytes, and up to half as long again. The figure is the console's own bound
 * on a save file (112 KiB since MUN OS v0.1.0-dev.5; 80 KiB before). */
#define MUN_SAVE_LOAD_MAX_BYTES 114688u

/* The save envelope this client reads (the MUN naming generation). */
#define MUN_SAVE_FORMAT "mun-save/1"

typedef struct mun_session mun_session; /* opaque; see mun_open() */

typedef enum mun_status {
    MUN_OK = 0,
    MUN_UNAVAILABLE = 1, /* not in a MUN session, or this card has no such feature */
    MUN_NO_SAVE = 2,     /* the card holds no save for this game: a new game */
    MUN_DAMAGED = 3,     /* a save file exists and is not a save this client can read */
    MUN_REFUSED = 4,     /* the console answered with an error; see mun_save_result.code */
    MUN_TIMEOUT = 5,     /* no answer in time; the save may or may not be on the card */
    MUN_IO = 6,          /* a file or socket error, or the console closed without answering */
    MUN_PROTOCOL = 7,    /* an answer this client cannot read */
    MUN_ARGUMENT = 8,    /* a NULL or malformed argument, or a payload that is not one JSON object */
    MUN_TOO_LARGE = 9,   /* the payload exceeds MUN_SAVE_MAX_BYTES, or the caller's buffer is too small */
    MUN_NO_MEMORY = 10,
    MUN_ABSENT = 11      /* mun_json_int(): the object has no member of that name */
} mun_status;

/* Facts the console publishes to the game (docs/runtime.md, "Environment"). */
typedef enum mun_fact {
    MUN_FACT_CARD_ID = 0,     /* MUN_CARD_ID */
    MUN_FACT_CONTENT_VERSION, /* MUN_CONTENT_VERSION */
    MUN_FACT_SESSION,         /* MUN_SESSION */
    MUN_FACT_RUNTIME_PROFILE, /* MUN_RUNTIME_PROFILE */
    MUN_FACT_CONTENT_DIR,     /* MUN_CONTENT_DIR: only on a card with content.access = "mount" */
    MUN_FACT_DISPLAY_MODE,    /* MUN_DISPLAY_MODE: "WIDTHxHEIGHT", GL profile only */
    MUN_FACT_SAVE_FILE,       /* MUN_SAVE_FILE */
    MUN_FACT_SAVE_SOCKET,     /* MUN_SAVE_SOCKET */
    MUN_FACT_COUNT_
} mun_fact;

typedef struct mun_save_info {
    uint32_t schema;   /* the save schema the game declared when it saved */
    uint8_t recovered; /* 1: the console handed out the previous save because the current one was missing */
} mun_save_info;

typedef struct mun_save_result {
    char code[32];     /* the console's stable code ("no_space", "card_removed", ...); "" on success */
    char message[192]; /* the console's sentence for that code, UTF-8, possibly truncated; "" on success */
    uint64_t bytes;    /* on success, the bytes of the save the card now holds */
} mun_save_result;

/* Reads the session's facts from the environment, copying them. Succeeds
 * outside a MUN session too (every fact is then absent). Returns
 * MUN_ARGUMENT for a NULL `out`, MUN_NO_MEMORY if allocation fails; `*out`
 * is NULL on failure. */
mun_status mun_open(mun_session **out);

/* Frees a session. Accepts NULL. */
void mun_close(mun_session *session);

/* 1 inside a MUN game session (MUN_SESSION is set and not empty), else 0. */
int mun_in_session(const mun_session *session);

/* The fact's value, or NULL when the console did not publish it. The string
 * belongs to the session and is valid until mun_close(). */
const char *mun_get(const mun_session *session, mun_fact fact);

/* Writes "<MUN_CONTENT_DIR>/<relative>" into `path`. `relative` must be a
 * plain relative path: no leading '/', no empty, "." or ".." segment.
 * MUN_UNAVAILABLE without a content directory, MUN_ARGUMENT for a bad
 * `relative`, MUN_TOO_LARGE if it does not fit. The content is read-only. */
mun_status mun_content_path(const mun_session *session, const char *relative, char *path, size_t capacity);

/* Reads the save the console restored for this session and copies the
 * game's own payload (one JSON object: the value the game saved, in the
 * console's spelling) into `payload`, NUL-terminated; `*length` excludes the
 * terminator.
 *
 *   MUN_OK           a save was read; `info` is filled.
 *   MUN_NO_SAVE      there is no save: start a new game.
 *   MUN_UNAVAILABLE  the console gave no save file (not in a session, or the
 *                    card saves by directory).
 *   MUN_DAMAGED      the file is not a mun-save/1 envelope with an object
 *                    payload. Start without it and do not save over it on
 *                    your own: the console keeps it until the player saves
 *                    on purpose.
 *   MUN_TOO_LARGE    the payload does not fit `capacity`
 *                    (MUN_SAVE_LOAD_MAX_BYTES always fits one).
 *   MUN_IO           the file could not be read.
 *
 * `payload` is written only on MUN_OK. `info` may be NULL. */
mun_status mun_save_load(mun_session *session, char *payload, size_t capacity, size_t *length,
                         mun_save_info *info);

/* Asks the console to save `json` (one JSON object of at most
 * MUN_SAVE_MAX_BYTES bytes) under the game's save `schema` (1 or more), and
 * waits for its answer. The console answers only once the card holds the
 * save, or with the reason it does not: show "saved" only after MUN_OK.
 *
 *   MUN_OK           the card holds the save; result->bytes is set.
 *   MUN_REFUSED      the console said no; result->code says why. The save
 *                    the card held before is intact.
 *   MUN_UNAVAILABLE  no save socket: the game can play but not save.
 *   MUN_TIMEOUT      no answer within timeout_ms (the console itself allows
 *                    a card 20 s). The outcome is unknown until the next
 *                    start reads the save back.
 *   MUN_IO           the socket failed or the console closed it without an
 *                    answer (the session is ending, or the request was not
 *                    accepted as a line). Outcome unknown, as above.
 *   MUN_ARGUMENT, MUN_TOO_LARGE, MUN_PROTOCOL as named.
 *
 * One save at a time per session: a second request while one is in flight
 * is refused with "save_busy". `result` may be NULL. Blocks; no retry is
 * made here. `timeout_ms` below 1 is treated as 1. */
mun_status mun_save_write(mun_session *session, uint32_t schema, const char *json, size_t length,
                          int32_t timeout_ms, mun_save_result *result);

/* A helper for small payloads: the integer value of the member `key` of the
 * JSON object in `json`. MUN_OK and `*value` on success; MUN_ABSENT when
 * the object has no such member; MUN_DAMAGED when `json` is not one JSON
 * object or the member is not an integer that fits int64_t. `key` is
 * compared byte for byte with member names written without escapes. */
mun_status mun_json_int(const char *json, size_t length, const char *key, int64_t *value);

/* A short constant name for a status, for logs ("ok", "refused", ...). */
const char *mun_status_name(mun_status status);

#ifdef __cplusplus
}
#endif

#endif /* MUN_H */
