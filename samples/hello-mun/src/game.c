#include "game.h"

#include <stdio.h>

#include "mun.h"

void game_init(struct game *game)
{
    game->count = 0;
    game->saves = 0;
    game->dirty = 0;
}

int game_change(struct game *game, int delta)
{
    int64_t next = game->count + delta;
    if (next < 0)
        next = 0;
    if (next > HELLO_COUNT_MAX)
        next = HELLO_COUNT_MAX;
    if (next == game->count)
        return 0;
    game->count = next;
    game->dirty = 1;
    return 1;
}

int game_payload(const struct game *game, char *out, size_t capacity)
{
    int written = snprintf(out, capacity, "{\"count\":%lld,\"saves\":%lld}", (long long)game->count,
                           (long long)(game->saves + 1));
    return (written < 0 || (size_t)written >= capacity) ? -1 : written;
}

int game_restore(struct game *game, const char *payload, size_t length, uint32_t schema)
{
    int64_t count, saves;
    if (schema != HELLO_SAVE_SCHEMA)
        return -1; /* a later version of the game wrote it: not ours to guess at */
    if (mun_json_int(payload, length, "count", &count) != MUN_OK ||
        mun_json_int(payload, length, "saves", &saves) != MUN_OK)
        return -1;
    if (count < 0 || count > HELLO_COUNT_MAX || saves < 0)
        return -1;
    game->count = count;
    game->saves = saves;
    game->dirty = 0;
    return 0;
}

void game_saved(struct game *game, const char *payload, size_t length)
{
    int64_t count, saves;
    if (mun_json_int(payload, length, "saves", &saves) == MUN_OK)
        game->saves = saves;
    /* Only what was saved is clean: the player may have changed the count
     * while the console was writing. */
    if (mun_json_int(payload, length, "count", &count) == MUN_OK && count == game->count)
        game->dirty = 0;
}
