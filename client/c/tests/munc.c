// Copyright 2026 Iván Moreno Mendoza
// SPDX-License-Identifier: Apache-2.0

/* munc: a command-line face of the client, for the tests. Each command
 * prints "status=<name>" and the fields the call filled, one per line.
 *
 *   munc facts                 every fact the session publishes
 *   munc path RELATIVE         mun_content_path()
 *   munc load [CAPACITY]       mun_save_load(); the payload after "payload="
 *   munc write SCHEMA TIMEOUT  mun_save_write() of the payload on standard input
 *   munc int KEY               mun_json_int() of the JSON on standard input
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "mun.h"

static char input[MUN_SAVE_MAX_BYTES * 2];
static char payload[MUN_SAVE_LOAD_MAX_BYTES];

static size_t read_input(void)
{
    return fread(input, 1, sizeof input, stdin);
}

int main(int argc, char **argv)
{
    mun_session *session = NULL;
    mun_status status = mun_open(&session);
    if (status != MUN_OK || argc < 2) {
        printf("status=%s\n", mun_status_name(status == MUN_OK ? MUN_ARGUMENT : status));
        return 2;
    }
    if (strcmp(argv[1], "facts") == 0) {
        static const char *names[] = {"card_id", "content_version", "session", "runtime_profile",
                                      "content_dir", "display_mode", "save_file", "save_socket"};
        printf("in_session=%d\n", mun_in_session(session));
        for (int i = 0; i < MUN_FACT_COUNT_; i++) {
            const char *value = mun_get(session, (mun_fact)i);
            printf("%s=%s\n", names[i], value ? value : "(absent)");
        }
        printf("out_of_range=%s\n", mun_get(session, MUN_FACT_COUNT_) ? "value" : "(absent)");
    } else if (strcmp(argv[1], "path") == 0 && argc == 3) {
        char path[64];
        status = mun_content_path(session, argv[2], path, sizeof path);
        printf("status=%s\n", mun_status_name(status));
        if (status == MUN_OK)
            printf("path=%s\n", path);
    } else if (strcmp(argv[1], "load") == 0) {
        size_t capacity = argc > 2 ? (size_t)strtoul(argv[2], NULL, 10) : sizeof payload;
        size_t length = 0;
        mun_save_info info = {0, 0};
        memset(payload, '#', 8);
        payload[8] = '\0';
        status = mun_save_load(session, payload, capacity > sizeof payload ? sizeof payload : capacity, &length, &info);
        printf("status=%s\n", mun_status_name(status));
        if (status == MUN_OK)
            printf("schema=%u\nrecovered=%u\nlength=%zu\npayload=%s\n", (unsigned)info.schema, (unsigned)info.recovered,
                   length, payload);
        else
            printf("untouched=%d\n", strcmp(payload, "########") == 0);
    } else if (strcmp(argv[1], "write") == 0 && argc == 4) {
        mun_save_result result;
        size_t length = read_input();
        status = mun_save_write(session, (uint32_t)strtoul(argv[2], NULL, 10), input, length, atoi(argv[3]), &result);
        printf("status=%s\ncode=%s\nmessage=%s\nbytes=%llu\n", mun_status_name(status), result.code, result.message,
               (unsigned long long)result.bytes);
    } else if (strcmp(argv[1], "int") == 0 && argc == 3) {
        int64_t value = -7777;
        size_t length = read_input();
        status = mun_json_int(input, length, argv[2], &value);
        printf("status=%s\nvalue=%lld\n", mun_status_name(status), (long long)value);
    } else {
        printf("status=argument\n");
    }
    mun_close(session);
    mun_close(NULL);
    return 0;
}
