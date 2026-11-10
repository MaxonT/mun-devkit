/* Host checks of the sample's own logic: the game state, its save payload
 * and the configuration reader. No SDL and no console. */
#include <stdio.h>
#include <string.h>

#include "config.h"
#include "game.h"

static int failures;

#define CHECK(condition) \
    do { \
        if (!(condition)) { \
            printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #condition); \
            failures++; \
        } \
    } while (0)

int main(void)
{
    struct game game;
    char payload[128];

    game_init(&game);
    CHECK(game.count == 0 && game.saves == 0 && !game.dirty);
    CHECK(game_change(&game, -1) == 0 && game.count == 0 && !game.dirty);
    CHECK(game_change(&game, 3) == 1 && game.count == 3 && game.dirty);
    CHECK(game_change(&game, 100000) == 1 && game.count == HELLO_COUNT_MAX);
    CHECK(game_change(&game, 1) == 0);

    game_init(&game);
    game_change(&game, 7);
    int length = game_payload(&game, payload, sizeof payload);
    CHECK(length > 0 && strcmp(payload, "{\"count\":7,\"saves\":1}") == 0);
    CHECK(game_payload(&game, payload, 4) == -1);
    game_payload(&game, payload, sizeof payload);

    /* The player moves on while the console writes: the save is counted,
     * the game stays dirty. */
    game_change(&game, 1);
    game_saved(&game, payload, (size_t)length);
    CHECK(game.saves == 1 && game.dirty && game.count == 8);
    length = game_payload(&game, payload, sizeof payload);
    game_saved(&game, payload, (size_t)length);
    CHECK(game.saves == 2 && !game.dirty);

    struct game restored;
    game_init(&restored);
    CHECK(game_restore(&restored, payload, (size_t)length, HELLO_SAVE_SCHEMA) == 0);
    CHECK(restored.count == 8 && restored.saves == 2 && !restored.dirty);
    CHECK(game_restore(&restored, payload, (size_t)length, HELLO_SAVE_SCHEMA + 1) == -1);
    CHECK(restored.count == 8);
    const char *wrong[] = {"{\"count\":-1,\"saves\":0}", "{\"count\":10000,\"saves\":0}", "{\"count\":1}",
                           "{\"count\":\"1\",\"saves\":0}", "[1]", "{\"count\":1,\"saves\":0", ""};
    for (size_t i = 0; i < sizeof wrong / sizeof wrong[0]; i++) {
        CHECK(game_restore(&restored, wrong[i], strlen(wrong[i]), HELLO_SAVE_SCHEMA) == -1);
        CHECK(restored.count == 8 && restored.saves == 2);
    }

    struct config config;
    config_defaults(&config);
    const char *text = "# a comment\n title =  MY GAME \nstep=5\nlab_keys = 1\nbackground = #FF0080\n"
                       "ink = red\nstep = 0\nunknown = 3\nno equals sign\n";
    config_parse(&config, text, strlen(text));
    CHECK(strcmp(config.title, "MY GAME") == 0);
    CHECK(config.step == 5 && config.lab_keys == 1);
    CHECK(config.background.r > 0.99f && config.background.g < 0.01f && config.background.b > 0.5f);
    CHECK(config.ink.r > 0.9f && config.ink.g > 0.9f); /* a bad colour keeps the default */
    CHECK(!config.loaded);
    CHECK(config_load(&config, "/nonexistent/hello.cfg") == 0);

    if (failures) {
        printf("%d check(s) failed\n", failures);
        return 1;
    }
    printf("hello-mun game and config: all checks passed\n");
    return 0;
}
