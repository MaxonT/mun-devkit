/* mun.c — see mun.h. C11 and POSIX; no other dependency. */
#define _POSIX_C_SOURCE 200809L
#define _DARWIN_C_SOURCE 1

#include "mun.h"

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#ifndef MSG_NOSIGNAL
#define MSG_NOSIGNAL 0 /* macOS: SO_NOSIGPIPE on the socket instead */
#endif

/* The envelope the console writes around a payload (docs/saves.md): the
 * payload plus names, times and identifiers. The console itself reads at most
 * this many bytes of a single-object save, so no payload is longer. */
#define SAVE_FILE_MAX_BYTES MUN_SAVE_LOAD_MAX_BYTES
/* One answer line: {"type":"saved","ok":false,"error":{"code":…,"message":…,"detail":…}}. */
#define REPLY_MAX_BYTES 4096u
#define JSON_MAX_DEPTH 64 /* as deep as the console itself reads */

static const char *const FACT_NAMES[MUN_FACT_COUNT_] = {
    "MUN_CARD_ID",      "MUN_CONTENT_VERSION", "MUN_SESSION",   "MUN_RUNTIME_PROFILE",
    "MUN_CONTENT_DIR",  "MUN_DISPLAY_MODE",    "MUN_SAVE_FILE", "MUN_SAVE_SOCKET",
};

struct mun_session {
    char *facts[MUN_FACT_COUNT_]; /* owned copies; NULL when absent or empty */
};

/* ------------------------------------------------------------------ JSON
 * A strict scanner over a bounded buffer: it validates and locates values,
 * and never interprets more than it is asked for. Anything unexpected is
 * malformed; nothing is guessed. */

struct scan {
    const char *p;
    const char *end;
    int depth;
};

static void skip_ws(struct scan *s)
{
    while (s->p < s->end && (*s->p == ' ' || *s->p == '\t' || *s->p == '\n' || *s->p == '\r'))
        s->p++;
}

static int is_hex(char c)
{
    return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F');
}

static int skip_string(struct scan *s)
{
    if (s->p >= s->end || *s->p != '"')
        return -1;
    s->p++;
    while (s->p < s->end) {
        unsigned char c = (unsigned char)*s->p;
        if (c == '"') {
            s->p++;
            return 0;
        }
        if (c < 0x20)
            return -1; /* raw control characters are not JSON */
        if (c == '\\') {
            s->p++;
            if (s->p >= s->end)
                return -1;
            if (*s->p == 'u') {
                if (s->end - s->p < 5)
                    return -1;
                for (int i = 1; i <= 4; i++)
                    if (!is_hex(s->p[i]))
                        return -1;
                s->p += 4;
            } else if (!strchr("\"\\/bfnrt", *s->p)) {
                return -1;
            }
        }
        s->p++;
    }
    return -1;
}

static int skip_digits(struct scan *s)
{
    const char *start = s->p;
    while (s->p < s->end && *s->p >= '0' && *s->p <= '9')
        s->p++;
    return s->p > start ? 0 : -1;
}

static int skip_number(struct scan *s)
{
    if (s->p < s->end && *s->p == '-')
        s->p++;
    if (s->p >= s->end)
        return -1;
    if (*s->p == '0') {
        s->p++;
    } else if (skip_digits(s) < 0) {
        return -1;
    }
    if (s->p < s->end && *s->p == '.') {
        s->p++;
        if (skip_digits(s) < 0)
            return -1;
    }
    if (s->p < s->end && (*s->p == 'e' || *s->p == 'E')) {
        s->p++;
        if (s->p < s->end && (*s->p == '+' || *s->p == '-'))
            s->p++;
        if (skip_digits(s) < 0)
            return -1;
    }
    return 0;
}

static int skip_literal(struct scan *s, const char *word)
{
    size_t n = strlen(word);
    if ((size_t)(s->end - s->p) < n || memcmp(s->p, word, n) != 0)
        return -1;
    s->p += n;
    return 0;
}

static int skip_value(struct scan *s);

static int skip_array(struct scan *s)
{
    s->p++; /* '[' */
    skip_ws(s);
    if (s->p < s->end && *s->p == ']') {
        s->p++;
        return 0;
    }
    for (;;) {
        if (skip_value(s) < 0)
            return -1;
        skip_ws(s);
        if (s->p >= s->end)
            return -1;
        if (*s->p == ',') {
            s->p++;
            continue;
        }
        if (*s->p == ']') {
            s->p++;
            return 0;
        }
        return -1;
    }
}

/* Called for each member of an object with its raw name (between the quotes,
 * escapes untouched) and its raw value. Returns 0 to go on, non-zero to stop
 * the scan as malformed. */
typedef int (*member_fn)(void *context, const char *name, size_t name_length, const char *value,
                         size_t value_length);

static int scan_members(struct scan *s, member_fn each, void *context)
{
    if (s->p >= s->end || *s->p != '{')
        return -1;
    s->p++;
    skip_ws(s);
    if (s->p < s->end && *s->p == '}') {
        s->p++;
        return 0;
    }
    for (;;) {
        skip_ws(s);
        const char *name = s->p + 1;
        if (skip_string(s) < 0)
            return -1;
        size_t name_length = (size_t)(s->p - 1 - name);
        skip_ws(s);
        if (s->p >= s->end || *s->p != ':')
            return -1;
        s->p++;
        skip_ws(s);
        const char *value = s->p;
        if (skip_value(s) < 0)
            return -1;
        if (each && each(context, name, name_length, value, (size_t)(s->p - value)) != 0)
            return -1;
        skip_ws(s);
        if (s->p >= s->end)
            return -1;
        if (*s->p == ',') {
            s->p++;
            continue;
        }
        if (*s->p == '}') {
            s->p++;
            return 0;
        }
        return -1;
    }
}

static int skip_value(struct scan *s)
{
    skip_ws(s);
    if (s->p >= s->end)
        return -1;
    int result;
    switch (*s->p) {
    case '{':
    case '[':
        if (++s->depth > JSON_MAX_DEPTH)
            return -1;
        result = *s->p == '{' ? scan_members(s, NULL, NULL) : skip_array(s);
        s->depth--;
        return result;
    case '"':
        return skip_string(s);
    case 't':
        return skip_literal(s, "true");
    case 'f':
        return skip_literal(s, "false");
    case 'n':
        return skip_literal(s, "null");
    default:
        return skip_number(s);
    }
}

/* `text` is exactly one JSON object, with only white space around it. */
static int scan_object(const char *text, size_t length, member_fn each, void *context)
{
    struct scan s = {text, text + length, 1};
    skip_ws(&s);
    if (scan_members(&s, each, context) < 0)
        return -1;
    skip_ws(&s);
    return s.p == s.end ? 0 : -1;
}

static int name_is(const char *name, size_t length, const char *wanted)
{
    return strlen(wanted) == length && memcmp(name, wanted, length) == 0;
}

/* The value of a JSON integer token: no fraction, no exponent, within int64_t. */
static int parse_int(const char *value, size_t length, int64_t *out)
{
    size_t i = 0;
    int negative = 0;
    uint64_t magnitude = 0;
    if (i < length && value[i] == '-') {
        negative = 1;
        i++;
    }
    if (i >= length)
        return -1;
    for (; i < length; i++) {
        if (value[i] < '0' || value[i] > '9')
            return -1;
        unsigned digit = (unsigned)(value[i] - '0');
        if (magnitude > (UINT64_MAX - digit) / 10)
            return -1;
        magnitude = magnitude * 10 + digit;
    }
    if (negative) {
        if (magnitude > (uint64_t)INT64_MAX + 1)
            return -1;
        *out = magnitude == (uint64_t)INT64_MAX + 1 ? INT64_MIN : -(int64_t)magnitude;
    } else {
        if (magnitude > (uint64_t)INT64_MAX)
            return -1;
        *out = (int64_t)magnitude;
    }
    return 0;
}

static unsigned hex_value(char c)
{
    if (c >= '0' && c <= '9')
        return (unsigned)(c - '0');
    if (c >= 'a' && c <= 'f')
        return (unsigned)(c - 'a') + 10;
    return (unsigned)(c - 'A') + 10;
}

static size_t put_utf8(uint32_t cp, char out[4])
{
    if (cp < 0x80) {
        out[0] = (char)cp;
        return 1;
    }
    if (cp < 0x800) {
        out[0] = (char)(0xC0 | (cp >> 6));
        out[1] = (char)(0x80 | (cp & 0x3F));
        return 2;
    }
    if (cp < 0x10000) {
        out[0] = (char)(0xE0 | (cp >> 12));
        out[1] = (char)(0x80 | ((cp >> 6) & 0x3F));
        out[2] = (char)(0x80 | (cp & 0x3F));
        return 3;
    }
    out[0] = (char)(0xF0 | (cp >> 18));
    out[1] = (char)(0x80 | ((cp >> 12) & 0x3F));
    out[2] = (char)(0x80 | ((cp >> 6) & 0x3F));
    out[3] = (char)(0x80 | (cp & 0x3F));
    return 4;
}

/* Decodes the JSON string token `value` (quotes included, already validated)
 * into `out` as UTF-8, NUL-terminated, cutting at a character boundary when
 * it does not fit. An unpaired surrogate becomes U+FFFD. */
static void decode_string(const char *value, size_t length, char *out, size_t capacity)
{
    size_t used = 0;
    const char *p = value + 1, *end = value + length - 1;
    if (capacity == 0)
        return;
    while (p < end) {
        uint32_t cp;
        char utf8[4];
        size_t n;
        if (*p != '\\') {
            /* Copy one UTF-8 sequence whole. */
            unsigned char lead = (unsigned char)*p;
            n = lead < 0x80 ? 1 : lead >= 0xF0 ? 4 : lead >= 0xE0 ? 3 : lead >= 0xC0 ? 2 : 1;
            if ((size_t)(end - p) < n)
                n = (size_t)(end - p);
            if (used + n >= capacity)
                break;
            memcpy(out + used, p, n);
            used += n;
            p += n;
            continue;
        }
        p++;
        switch (*p) {
        case 'b': cp = '\b'; p++; break;
        case 'f': cp = '\f'; p++; break;
        case 'n': cp = '\n'; p++; break;
        case 'r': cp = '\r'; p++; break;
        case 't': cp = '\t'; p++; break;
        case 'u':
            cp = hex_value(p[1]) << 12 | hex_value(p[2]) << 8 | hex_value(p[3]) << 4 | hex_value(p[4]);
            p += 5;
            if (cp >= 0xD800 && cp <= 0xDBFF && end - p >= 6 && p[0] == '\\' && p[1] == 'u') {
                uint32_t low = hex_value(p[2]) << 12 | hex_value(p[3]) << 8 | hex_value(p[4]) << 4 |
                               hex_value(p[5]);
                if (low >= 0xDC00 && low <= 0xDFFF) {
                    cp = 0x10000 + ((cp - 0xD800) << 10) + (low - 0xDC00);
                    p += 6;
                }
            }
            if (cp >= 0xD800 && cp <= 0xDFFF)
                cp = 0xFFFD;
            break;
        default: cp = (unsigned char)*p; p++; break; /* '"', '\\', '/' */
        }
        n = put_utf8(cp, utf8);
        if (used + n >= capacity)
            break;
        memcpy(out + used, utf8, n);
        used += n;
    }
    out[used] = '\0';
}

/* ------------------------------------------------------------------ session */

mun_status mun_open(mun_session **out)
{
    if (!out)
        return MUN_ARGUMENT;
    *out = NULL;
    mun_session *session = calloc(1, sizeof *session);
    if (!session)
        return MUN_NO_MEMORY;
    for (int i = 0; i < MUN_FACT_COUNT_; i++) {
        const char *value = getenv(FACT_NAMES[i]);
        if (!value || !*value)
            continue;
        size_t n = strlen(value) + 1;
        session->facts[i] = malloc(n);
        if (!session->facts[i]) {
            mun_close(session);
            return MUN_NO_MEMORY;
        }
        memcpy(session->facts[i], value, n);
    }
    *out = session;
    return MUN_OK;
}

void mun_close(mun_session *session)
{
    if (!session)
        return;
    for (int i = 0; i < MUN_FACT_COUNT_; i++)
        free(session->facts[i]);
    free(session);
}

const char *mun_get(const mun_session *session, mun_fact fact)
{
    if (!session || (int)fact < 0 || fact >= MUN_FACT_COUNT_)
        return NULL;
    return session->facts[fact];
}

int mun_in_session(const mun_session *session)
{
    return mun_get(session, MUN_FACT_SESSION) != NULL;
}

mun_status mun_content_path(const mun_session *session, const char *relative, char *path, size_t capacity)
{
    if (!session || !relative || !path || capacity == 0)
        return MUN_ARGUMENT;
    const char *dir = mun_get(session, MUN_FACT_CONTENT_DIR);
    if (!dir)
        return MUN_UNAVAILABLE;
    /* Every segment must be a real name: the path stays inside the content. */
    if (!*relative || *relative == '/')
        return MUN_ARGUMENT;
    for (const char *segment = relative; *segment;) {
        size_t n = strcspn(segment, "/");
        if (n == 0 || (n == 1 && segment[0] == '.') || (n == 2 && segment[0] == '.' && segment[1] == '.'))
            return MUN_ARGUMENT;
        segment += n;
        if (*segment == '/') {
            segment++;
            if (!*segment)
                return MUN_ARGUMENT; /* a trailing slash names no file */
        }
    }
    int written = snprintf(path, capacity, "%s/%s", dir, relative);
    if (written < 0 || (size_t)written >= capacity)
        return MUN_TOO_LARGE;
    return MUN_OK;
}

/* ------------------------------------------------------------------ save: read */

struct envelope {
    const char *payload;
    size_t payload_length;
    int64_t schema;
    int have_format, have_schema, have_payload, recovered;
};

static int envelope_member(void *context, const char *name, size_t name_length, const char *value,
                           size_t value_length)
{
    struct envelope *e = context;
    if (name_is(name, name_length, "format")) {
        static const char wanted[] = "\"" MUN_SAVE_FORMAT "\"";
        if (e->have_format++ || value_length != sizeof wanted - 1 || memcmp(value, wanted, value_length) != 0)
            return -1;
    } else if (name_is(name, name_length, "schema")) {
        if (e->have_schema++ || parse_int(value, value_length, &e->schema) < 0 || e->schema < 1 ||
            e->schema > (int64_t)UINT32_MAX)
            return -1;
    } else if (name_is(name, name_length, "payload")) {
        if (e->have_payload++ || value_length == 0 || value[0] != '{')
            return -1;
        e->payload = value;
        e->payload_length = value_length;
    } else if (name_is(name, name_length, "recovered_from")) {
        e->recovered = 1;
    }
    return 0;
}

mun_status mun_save_load(mun_session *session, char *payload, size_t capacity, size_t *length,
                         mun_save_info *info)
{
    if (!session || !payload || !length || capacity == 0)
        return MUN_ARGUMENT;
    const char *path = mun_get(session, MUN_FACT_SAVE_FILE);
    if (!path)
        return MUN_UNAVAILABLE;
    FILE *file = fopen(path, "rb");
    if (!file)
        return errno == ENOENT ? MUN_NO_SAVE : MUN_IO;
    char *text = malloc(SAVE_FILE_MAX_BYTES + 1);
    if (!text) {
        fclose(file);
        return MUN_NO_MEMORY;
    }
    size_t size = fread(text, 1, SAVE_FILE_MAX_BYTES + 1, file);
    int failed = ferror(file);
    fclose(file);
    mun_status status;
    struct envelope e = {0};
    if (failed) {
        status = MUN_IO;
    } else if (size > SAVE_FILE_MAX_BYTES) {
        status = MUN_DAMAGED; /* larger than any save the console writes */
    } else if (scan_object(text, size, envelope_member, &e) < 0 || !e.have_format || !e.have_schema ||
               !e.have_payload) {
        status = MUN_DAMAGED;
    } else if (e.payload_length + 1 > capacity) {
        status = MUN_TOO_LARGE;
    } else {
        memcpy(payload, e.payload, e.payload_length);
        payload[e.payload_length] = '\0';
        *length = e.payload_length;
        if (info) {
            info->schema = (uint32_t)e.schema;
            info->recovered = (uint8_t)(e.recovered ? 1 : 0);
        }
        status = MUN_OK;
    }
    free(text);
    return status;
}

/* ------------------------------------------------------------------ save: write */

static int64_t now_ms(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (int64_t)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}

/* Waits for `events` on `fd` until `deadline`. 1 ready, 0 timed out, -1 error. */
static int wait_for(int fd, short events, int64_t deadline)
{
    for (;;) {
        int64_t left = deadline - now_ms();
        if (left <= 0)
            return 0;
        struct pollfd p = {fd, events, 0};
        int n = poll(&p, 1, left > 1000 ? 1000 : (int)left);
        if (n > 0)
            return 1; /* readable, writable, or an error the next call reports */
        if (n < 0 && errno != EINTR)
            return -1;
    }
}

static mun_status connect_to(const char *path, int64_t deadline, int *out)
{
    struct sockaddr_un address;
    memset(&address, 0, sizeof address);
    address.sun_family = AF_UNIX;
    if (strlen(path) >= sizeof address.sun_path)
        return MUN_IO;
    memcpy(address.sun_path, path, strlen(path) + 1);
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0)
        return MUN_IO;
    int flags = fcntl(fd, F_GETFL, 0);
    if (flags < 0 || fcntl(fd, F_SETFL, flags | O_NONBLOCK) < 0 || fcntl(fd, F_SETFD, FD_CLOEXEC) < 0) {
        close(fd);
        return MUN_IO;
    }
#ifdef SO_NOSIGPIPE
    {
        int one = 1;
        (void)setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &one, sizeof one);
    }
#endif
    for (;;) {
        if (connect(fd, (struct sockaddr *)&address, sizeof address) == 0)
            break;
        if (errno == EISCONN)
            break;
        if (errno == EINTR)
            continue;
        if (errno == EINPROGRESS || errno == EALREADY) {
            int ready = wait_for(fd, POLLOUT, deadline);
            int error = 0;
            socklen_t n = sizeof error;
            if (ready == 1 && getsockopt(fd, SOL_SOCKET, SO_ERROR, &error, &n) == 0 && error == 0)
                break;
            close(fd);
            return ready == 0 ? MUN_TIMEOUT : MUN_IO;
        }
        if (errno == EAGAIN) {
            /* The listener's queue is full (Linux reports it this way for a
             * non-blocking UNIX socket); the console accepts within a tick. */
            struct timespec pause = {0, 10 * 1000 * 1000};
            if (now_ms() >= deadline) {
                close(fd);
                return MUN_TIMEOUT;
            }
            nanosleep(&pause, NULL);
            continue;
        }
        close(fd);
        return MUN_IO;
    }
    *out = fd;
    return MUN_OK;
}

static mun_status send_all(int fd, const char *data, size_t length, int64_t deadline)
{
    size_t sent = 0;
    while (sent < length) {
        ssize_t n = send(fd, data + sent, length - sent, MSG_NOSIGNAL);
        if (n > 0) {
            sent += (size_t)n;
            continue;
        }
        if (n < 0 && errno == EINTR)
            continue;
        if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
            int ready = wait_for(fd, POLLOUT, deadline);
            if (ready == 1)
                continue;
            return ready == 0 ? MUN_TIMEOUT : MUN_IO;
        }
        return MUN_IO;
    }
    return MUN_OK;
}

/* Reads one line (without its newline) into `line`. */
static mun_status receive_line(int fd, char *line, size_t capacity, size_t *length, int64_t deadline)
{
    size_t used = 0;
    for (;;) {
        char *newline = memchr(line, '\n', used);
        if (newline) {
            *length = (size_t)(newline - line);
            return MUN_OK;
        }
        if (used == capacity)
            return MUN_PROTOCOL;
        int ready = wait_for(fd, POLLIN, deadline);
        if (ready != 1)
            return ready == 0 ? MUN_TIMEOUT : MUN_IO;
        ssize_t n = recv(fd, line + used, capacity - used, 0);
        if (n > 0)
            used += (size_t)n;
        else if (n == 0)
            return MUN_IO; /* closed without an answer */
        else if (errno != EINTR && errno != EAGAIN && errno != EWOULDBLOCK)
            return MUN_IO;
    }
}

struct reply {
    mun_save_result *result;
    int have_type, have_ok, ok, have_code;
};

static int error_member(void *context, const char *name, size_t name_length, const char *value,
                        size_t value_length)
{
    struct reply *r = context;
    if (value_length < 2 || value[0] != '"')
        return 0; /* only strings are of interest here */
    if (name_is(name, name_length, "code")) {
        decode_string(value, value_length, r->result->code, sizeof r->result->code);
        r->have_code = r->result->code[0] != '\0';
    } else if (name_is(name, name_length, "message")) {
        decode_string(value, value_length, r->result->message, sizeof r->result->message);
    }
    return 0;
}

static int reply_member(void *context, const char *name, size_t name_length, const char *value,
                        size_t value_length)
{
    struct reply *r = context;
    if (name_is(name, name_length, "type")) {
        static const char wanted[] = "\"saved\"";
        if (value_length != sizeof wanted - 1 || memcmp(value, wanted, value_length) != 0)
            return -1;
        r->have_type = 1;
    } else if (name_is(name, name_length, "ok")) {
        if (value_length == 4 && memcmp(value, "true", 4) == 0)
            r->ok = 1;
        else if (value_length == 5 && memcmp(value, "false", 5) == 0)
            r->ok = 0;
        else
            return -1;
        r->have_ok = 1;
    } else if (name_is(name, name_length, "bytes")) {
        int64_t bytes;
        if (parse_int(value, value_length, &bytes) < 0 || bytes < 0)
            return -1;
        r->result->bytes = (uint64_t)bytes;
    } else if (name_is(name, name_length, "error")) {
        if (value_length == 0 || value[0] != '{' || scan_object(value, value_length, error_member, r) < 0)
            return -1;
    }
    return 0;
}

mun_status mun_save_write(mun_session *session, uint32_t schema, const char *json, size_t length,
                          int32_t timeout_ms, mun_save_result *result)
{
    mun_save_result local;
    if (!result)
        result = &local;
    memset(result, 0, sizeof *result);
    if (!session || !json || schema < 1)
        return MUN_ARGUMENT;
    if (length > MUN_SAVE_MAX_BYTES)
        return MUN_TOO_LARGE;
    if (scan_object(json, length, NULL, NULL) < 0)
        return MUN_ARGUMENT;
    const char *path = mun_get(session, MUN_FACT_SAVE_SOCKET);
    if (!path)
        return MUN_UNAVAILABLE;

    /* One line: {"type":"save","schema":N,"data":<payload>}. A line break in
     * the payload can only be white space between tokens (the scan refused
     * raw control characters inside strings), so it becomes a space. */
    char *request = malloc(length + 64);
    if (!request)
        return MUN_NO_MEMORY;
    int head = snprintf(request, 64, "{\"type\":\"save\",\"schema\":%lu,\"data\":", (unsigned long)schema);
    size_t size = (size_t)head;
    for (size_t i = 0; i < length; i++)
        request[size++] = (json[i] == '\n' || json[i] == '\r') ? ' ' : json[i];
    request[size++] = '}';
    request[size++] = '\n';

    int64_t deadline = now_ms() + (timeout_ms < 1 ? 1 : timeout_ms);
    int fd = -1;
    char line[REPLY_MAX_BYTES];
    size_t line_length = 0;
    mun_status status = connect_to(path, deadline, &fd);
    if (status == MUN_OK)
        status = send_all(fd, request, size, deadline);
    free(request);
    if (status == MUN_OK)
        status = receive_line(fd, line, sizeof line, &line_length, deadline);
    if (fd >= 0)
        close(fd);
    if (status != MUN_OK)
        return status;

    struct reply r = {result, 0, 0, 0, 0};
    if (scan_object(line, line_length, reply_member, &r) < 0 || !r.have_type || !r.have_ok) {
        memset(result, 0, sizeof *result);
        return MUN_PROTOCOL;
    }
    if (r.ok) {
        result->code[0] = result->message[0] = '\0';
        return MUN_OK;
    }
    if (!r.have_code) {
        memset(result, 0, sizeof *result);
        return MUN_PROTOCOL; /* a refusal must say why */
    }
    result->bytes = 0;
    return MUN_REFUSED;
}

/* ------------------------------------------------------------------ helpers */

struct lookup {
    const char *key;
    int64_t value;
    int found, bad;
};

static int lookup_member(void *context, const char *name, size_t name_length, const char *value,
                         size_t value_length)
{
    struct lookup *l = context;
    if (!name_is(name, name_length, l->key))
        return 0;
    if (l->found++ || parse_int(value, value_length, &l->value) < 0)
        l->bad = 1;
    return 0;
}

mun_status mun_json_int(const char *json, size_t length, const char *key, int64_t *value)
{
    if (!json || !key || !value)
        return MUN_ARGUMENT;
    struct lookup l = {key, 0, 0, 0};
    if (scan_object(json, length, lookup_member, &l) < 0 || l.bad)
        return MUN_DAMAGED;
    if (!l.found)
        return MUN_ABSENT;
    *value = l.value;
    return MUN_OK;
}

const char *mun_status_name(mun_status status)
{
    switch (status) {
    case MUN_OK: return "ok";
    case MUN_UNAVAILABLE: return "unavailable";
    case MUN_NO_SAVE: return "no_save";
    case MUN_DAMAGED: return "damaged";
    case MUN_REFUSED: return "refused";
    case MUN_TIMEOUT: return "timeout";
    case MUN_IO: return "io";
    case MUN_PROTOCOL: return "protocol";
    case MUN_ARGUMENT: return "argument";
    case MUN_TOO_LARGE: return "too_large";
    case MUN_NO_MEMORY: return "no_memory";
    case MUN_ABSENT: return "absent";
    }
    return "unknown";
}
