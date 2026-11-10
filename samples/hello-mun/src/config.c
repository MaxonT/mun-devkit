#include "config.h"

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CONFIG_MAX_BYTES 4096

void config_defaults(struct config *config)
{
    memset(config, 0, sizeof *config);
    snprintf(config->title, sizeof config->title, "MUN HELLO");
    config->background = (struct colour){0.086f, 0.125f, 0.169f};
    config->ink = (struct colour){0.937f, 0.914f, 0.867f};
    config->accent = (struct colour){0.890f, 0.604f, 0.388f};
    config->step = 1;
}

static int parse_colour(const char *value, struct colour *out)
{
    unsigned r, g, b;
    char tail;
    if (strlen(value) != 7 || sscanf(value, "#%2x%2x%2x%c", &r, &g, &b, &tail) != 3)
        return 0;
    *out = (struct colour){(float)r / 255.0f, (float)g / 255.0f, (float)b / 255.0f};
    return 1;
}

static void apply(struct config *config, const char *name, const char *value)
{
    if (strcmp(name, "title") == 0 && *value) {
        snprintf(config->title, sizeof config->title, "%s", value);
    } else if (strcmp(name, "background") == 0) {
        parse_colour(value, &config->background);
    } else if (strcmp(name, "ink") == 0) {
        parse_colour(value, &config->ink);
    } else if (strcmp(name, "accent") == 0) {
        parse_colour(value, &config->accent);
    } else if (strcmp(name, "step") == 0) {
        long step = strtol(value, NULL, 10);
        if (step >= 1 && step <= 100)
            config->step = (int)step;
    } else if (strcmp(name, "lab_keys") == 0) {
        config->lab_keys = strcmp(value, "1") == 0;
    }
}

static char *trim(char *text)
{
    while (*text && isspace((unsigned char)*text))
        text++;
    char *end = text + strlen(text);
    while (end > text && isspace((unsigned char)end[-1]))
        *--end = '\0';
    return text;
}

void config_parse(struct config *config, const char *text, size_t length)
{
    char line[256];
    size_t at = 0;
    while (at < length) {
        size_t n = 0;
        while (at < length && text[at] != '\n') {
            if (n + 1 < sizeof line)
                line[n++] = text[at];
            at++;
        }
        at++; /* the newline */
        line[n] = '\0';
        char *name = trim(line);
        if (!*name || *name == '#')
            continue;
        char *equals = strchr(name, '=');
        if (!equals)
            continue;
        *equals = '\0';
        apply(config, trim(name), trim(equals + 1));
    }
}

int config_load(struct config *config, const char *path)
{
    char text[CONFIG_MAX_BYTES];
    FILE *file = fopen(path, "rb");
    if (!file)
        return 0;
    size_t size = fread(text, 1, sizeof text, file);
    int failed = ferror(file);
    fclose(file);
    if (failed)
        return 0;
    config_parse(config, text, size);
    config->loaded = 1;
    return 1;
}
