/* The sample's configuration, read from the card's content. */
#ifndef HELLO_CONFIG_H
#define HELLO_CONFIG_H

#include <stddef.h>

struct colour {
    float r, g, b;
};

struct config {
    char title[48];
    struct colour background, ink, accent;
    int step;     /* 1..100 */
    int lab_keys; /* F and C end the game badly on purpose */
    int loaded;   /* 1 when the file was read from the content */
};

void config_defaults(struct config *config);

/* Applies the text of a configuration file (`name = value` lines, '#'
 * comments) over `config`. Unknown names and malformed values are ignored:
 * a bad file never stops the game. */
void config_parse(struct config *config, const char *text, size_t length);

/* Reads `path` and applies it. Returns 1 if the file was read, 0 otherwise. */
int config_load(struct config *config, const char *path);

#endif
