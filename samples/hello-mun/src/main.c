/* MUN Hello: the devkit's native sample.
 *
 * A complete game for the runtime profile linux-arm64-gl-v0, as small as one
 * can be and still do everything a game on a card does:
 *
 *   - reads its data from the card's content (MUN_CONTENT_DIR);
 *   - takes the keyboard and a game controller;
 *   - plays sound;
 *   - restores the save the console hands it, and asks the console to save,
 *     saying "saved" only when the console has confirmed it;
 *   - ends with exit code 0 when the player quits, and when the console asks
 *     it to stop (SIGTERM, which SDL delivers as a quit event).
 *
 * Controls: Up/Down or the D-pad or left stick change the count; Enter,
 * Space, S, or the controller's south button or Start save; Escape, Q or the
 * east button quit.
 *
 * For automated checks, two variables the console never sets:
 *   MUN_HELLO_HEADLESS=1   run without a window or sound device;
 *   MUN_HELLO_SCRIPT="up up save wait quit"   play these steps, one a frame:
 *       up, down, save, wait (until the save is answered), idle:N (N frames),
 *       quit, fail (exit 3), crash (abort). Without "quit" the game keeps
 *       running until it is stopped.
 *   MUN_HELLO_CAPTURE=FILE  write a frame as a PPM image: the second one, or
 *                           the one MUN_HELLO_CAPTURE_FRAME numbers (from 0);
 *   MUN_HELLO_WINDOW=WxH    open a window of that size instead of the whole
 *                           display (a look at it on a desktop).
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <SDL.h>

#include "config.h"
#include "draw.h"
#include "game.h"
#include "mun.h"
#include "sound.h"

#define SAVE_TIMEOUT_MS 25000 /* the console itself allows a card 20 s */
#define STICK_THRESHOLD 16000
#define STICK_REPEAT_MS 180

enum save_state {
    SAVE_NEW,         /* no save on the card */
    SAVE_RESTORED,    /* the card's save was loaded */
    SAVE_RECOVERED,   /* the console handed out the previous save */
    SAVE_DAMAGED,     /* a save exists and could not be read */
    SAVE_INCOMPATIBLE,/* a save of a schema this version does not know */
    SAVE_UNAVAILABLE, /* not in a MUN session: nothing to save to */
    SAVE_WRITING,     /* asked; waiting for the console */
    SAVE_DONE,        /* the console confirmed it */
    SAVE_REFUSED,     /* the console said no, or did not answer */
};

/* One save request, run on its own thread so that frames keep coming while
 * the card is written. The session is touched only by that thread while
 * `running` is set. */
struct save_job {
    mun_session *session;
    char payload[96];
    size_t length;
    mun_status status;
    mun_save_result result;
    SDL_atomic_t finished;
    SDL_Thread *thread;
    int running;
};

struct app {
    struct config config;
    struct game game;
    enum save_state save;
    char save_text[96];
    struct save_job job;
    int sound_ok, content_ok;
    int quit, exit_code;
    char controller_name[40];
    SDL_GameController *controller;
    Uint32 stick_at;
    int stick_direction;
};

static int save_thread(void *data)
{
    struct save_job *job = data;
    job->status = mun_save_write(job->session, HELLO_SAVE_SCHEMA, job->payload, job->length, SAVE_TIMEOUT_MS,
                                 &job->result);
    SDL_AtomicSet(&job->finished, 1);
    return 0;
}

static void save_begin(struct app *app)
{
    if (app->job.running)
        return; /* one save at a time: the console would answer save_busy */
    if (!mun_get(app->job.session, MUN_FACT_SAVE_SOCKET)) {
        app->save = SAVE_UNAVAILABLE;
        snprintf(app->save_text, sizeof app->save_text, "SAVE: NOT IN A MUN SESSION");
        return;
    }
    int length = game_payload(&app->game, app->job.payload, sizeof app->job.payload);
    if (length < 0)
        return;
    app->job.length = (size_t)length;
    SDL_AtomicSet(&app->job.finished, 0);
    app->job.thread = SDL_CreateThread(save_thread, "save", &app->job);
    if (!app->job.thread) {
        fprintf(stderr, "hello-mun: could not start the save thread: %s\n", SDL_GetError());
        return;
    }
    app->job.running = 1;
    app->save = SAVE_WRITING;
    snprintf(app->save_text, sizeof app->save_text, "SAVE: WRITING...");
    fprintf(stderr, "hello-mun: save requested: %s\n", app->job.payload);
}

/* Takes the answer of a finished save, if there is one. */
static void save_poll(struct app *app)
{
    if (!app->job.running || !SDL_AtomicGet(&app->job.finished))
        return;
    SDL_WaitThread(app->job.thread, NULL);
    app->job.running = 0;
    if (app->job.status == MUN_OK) {
        game_saved(&app->game, app->job.payload, app->job.length);
        app->save = SAVE_DONE;
        snprintf(app->save_text, sizeof app->save_text, "SAVED ON THE CARD (%llu BYTES)",
                 (unsigned long long)app->job.result.bytes);
        sound_play(SOUND_SAVED);
        fprintf(stderr, "hello-mun: save confirmed: %llu bytes\n", (unsigned long long)app->job.result.bytes);
    } else {
        const char *why = app->job.status == MUN_REFUSED ? app->job.result.code : mun_status_name(app->job.status);
        app->save = SAVE_REFUSED;
        snprintf(app->save_text, sizeof app->save_text, "NOT SAVED: %s", why);
        sound_play(SOUND_REFUSED);
        fprintf(stderr, "hello-mun: save not confirmed: %s (%s) %s\n", mun_status_name(app->job.status),
                app->job.result.code, app->job.result.message);
    }
}

static void restore(struct app *app)
{
    static char payload[MUN_SAVE_LOAD_MAX_BYTES];
    size_t length = 0;
    mun_save_info info = {0, 0};
    mun_status status = mun_save_load(app->job.session, payload, sizeof payload, &length, &info);
    switch (status) {
    case MUN_OK:
        if (game_restore(&app->game, payload, length, info.schema) == 0) {
            app->save = info.recovered ? SAVE_RECOVERED : SAVE_RESTORED;
            snprintf(app->save_text, sizeof app->save_text,
                     info.recovered ? "RESTORED THE PREVIOUS SAVE" : "RESTORED FROM THE CARD");
        } else {
            app->save = SAVE_INCOMPATIBLE;
            snprintf(app->save_text, sizeof app->save_text, "SAVE OF SCHEMA %u NOT UNDERSTOOD", (unsigned)info.schema);
        }
        break;
    case MUN_NO_SAVE:
        app->save = SAVE_NEW;
        snprintf(app->save_text, sizeof app->save_text, "NEW GAME");
        break;
    case MUN_UNAVAILABLE:
        app->save = SAVE_UNAVAILABLE;
        snprintf(app->save_text, sizeof app->save_text, "NOT IN A MUN SESSION");
        break;
    default:
        app->save = SAVE_DAMAGED;
        snprintf(app->save_text, sizeof app->save_text, "THE CARD'S SAVE IS DAMAGED");
        break;
    }
    fprintf(stderr, "hello-mun: restore: %s (count %lld, saves %lld)\n", mun_status_name(status),
            (long long)app->game.count, (long long)app->game.saves);
}

static void change(struct app *app, int direction)
{
    if (game_change(&app->game, direction * app->config.step))
        sound_play(SOUND_BLIP);
}

static void lab_fail(struct app *app)
{
    fprintf(stderr, "hello-mun: ending with exit code 3 on purpose\n");
    app->exit_code = 3;
    app->quit = 1;
}

static void lab_crash(void)
{
    fprintf(stderr, "hello-mun: aborting on purpose\n");
    fflush(stderr);
    abort();
}

static void controller_open(struct app *app, int index)
{
    if (app->controller || !SDL_IsGameController(index))
        return;
    app->controller = SDL_GameControllerOpen(index);
    if (!app->controller)
        return;
    const char *name = SDL_GameControllerName(app->controller);
    snprintf(app->controller_name, sizeof app->controller_name, "%s", name ? name : "CONTROLLER");
    fprintf(stderr, "hello-mun: controller connected: %s\n", app->controller_name);
}

static void controller_close(struct app *app, SDL_JoystickID which)
{
    if (!app->controller || SDL_JoystickInstanceID(SDL_GameControllerGetJoystick(app->controller)) != which)
        return;
    SDL_GameControllerClose(app->controller);
    app->controller = NULL;
    app->controller_name[0] = '\0';
    app->stick_direction = 0;
    fprintf(stderr, "hello-mun: controller disconnected\n");
}

static void handle_event(struct app *app, const SDL_Event *event)
{
    switch (event->type) {
    case SDL_QUIT: /* the window was closed, or the console sent SIGTERM */
        fprintf(stderr, "hello-mun: asked to stop\n");
        app->quit = 1;
        break;
    case SDL_KEYDOWN:
        switch (event->key.keysym.sym) {
        case SDLK_UP: change(app, +1); break;
        case SDLK_DOWN: change(app, -1); break;
        case SDLK_RETURN:
        case SDLK_SPACE:
        case SDLK_s:
            if (!event->key.repeat)
                save_begin(app);
            break;
        case SDLK_ESCAPE:
        case SDLK_q: app->quit = 1; break;
        case SDLK_f:
            if (app->config.lab_keys)
                lab_fail(app);
            break;
        case SDLK_c:
            if (app->config.lab_keys)
                lab_crash();
            break;
        default: break;
        }
        break;
    case SDL_CONTROLLERDEVICEADDED: controller_open(app, event->cdevice.which); break;
    case SDL_CONTROLLERDEVICEREMOVED: controller_close(app, event->cdevice.which); break;
    case SDL_CONTROLLERBUTTONDOWN:
        switch (event->cbutton.button) {
        case SDL_CONTROLLER_BUTTON_DPAD_UP: change(app, +1); break;
        case SDL_CONTROLLER_BUTTON_DPAD_DOWN: change(app, -1); break;
        case SDL_CONTROLLER_BUTTON_A: /* the south button, whatever is printed on it */
        case SDL_CONTROLLER_BUTTON_START: save_begin(app); break;
        case SDL_CONTROLLER_BUTTON_B: app->quit = 1; break;
        default: break;
        }
        break;
    default: break;
    }
}

/* The left stick behaves as a D-pad that repeats while it is held. */
static void poll_stick(struct app *app)
{
    if (!app->controller)
        return;
    int y = SDL_GameControllerGetAxis(app->controller, SDL_CONTROLLER_AXIS_LEFTY);
    int direction = y < -STICK_THRESHOLD ? +1 : y > STICK_THRESHOLD ? -1 : 0;
    Uint32 now = SDL_GetTicks();
    if (direction && (direction != app->stick_direction || now - app->stick_at >= STICK_REPEAT_MS)) {
        change(app, direction);
        app->stick_at = now;
    }
    app->stick_direction = direction;
}

/* ------------------------------------------------------------------ script */

struct script {
    const char *at;   /* the next step, or NULL when there is no script */
    int idle;         /* frames still to wait */
};

/* Plays one step of MUN_HELLO_SCRIPT. */
static void script_step(struct app *app, struct script *script)
{
    char word[24];
    if (!script->at)
        return;
    if (script->idle > 0) {
        script->idle--;
        return;
    }
    while (*script->at == ' ' || *script->at == ',')
        script->at++;
    if (!*script->at) {
        script->at = NULL;
        return;
    }
    size_t n = strcspn(script->at, " ,");
    if (n >= sizeof word)
        n = sizeof word - 1;
    memcpy(word, script->at, n);
    word[n] = '\0';
    if (strcmp(word, "wait") == 0) {
        if (app->job.running)
            return; /* stay on this step until the save is answered */
    } else if (strcmp(word, "up") == 0) {
        change(app, +1);
    } else if (strcmp(word, "down") == 0) {
        change(app, -1);
    } else if (strcmp(word, "save") == 0) {
        save_begin(app);
    } else if (strcmp(word, "quit") == 0) {
        app->quit = 1;
    } else if (strcmp(word, "fail") == 0) {
        lab_fail(app);
    } else if (strcmp(word, "crash") == 0) {
        lab_crash();
    } else if (strncmp(word, "idle:", 5) == 0) {
        script->idle = atoi(word + 5);
    } else {
        fprintf(stderr, "hello-mun: unknown script step \"%s\"\n", word);
    }
    script->at += strcspn(script->at, " ,");
}

/* ------------------------------------------------------------------ picture */

static struct colour dim(struct colour colour, struct colour towards, float amount)
{
    return (struct colour){colour.r + (towards.r - colour.r) * amount, colour.g + (towards.g - colour.g) * amount,
                           colour.b + (towards.b - colour.b) * amount};
}

static void lamp(float x, float y, float unit, int on, const char *label, const struct app *app)
{
    const struct colour good = {0.36f, 0.72f, 0.45f}, bad = {0.80f, 0.30f, 0.26f};
    draw_rect(x, y, unit * 2, unit * 2, on ? good : bad);
    draw_text(x + unit * 3, y + unit * 0.25f, unit * 0.3f, label, app->config.ink);
}

static void draw_frame(const struct app *app, int width, int height, const char *facts, unsigned long frames)
{
    const struct config *config = &app->config;
    const struct colour faint = dim(config->ink, config->background, 0.55f);
    float unit = (float)height / 54.0f; /* 20 px at 1080p */
    float margin = unit * 4;
    char line[160];

    draw_begin(width, height, config->background);
    draw_rect(0, 0, (float)width, unit, config->accent);
    draw_text(margin, unit * 4, unit * 0.8f, config->title, config->ink);
    draw_text(margin, unit * 9.5f, unit * 0.35f, "THE MUN DEVKIT'S NATIVE SAMPLE", faint);

    /* The count, large and centred, with a mark while it is unsaved. */
    snprintf(line, sizeof line, "%lld", (long long)app->game.count);
    float pixel = unit * 2.4f;
    float number_width = draw_text_width(pixel, line);
    float number_x = ((float)width - number_width) / 2, number_y = unit * 16;
    draw_text(number_x, number_y, pixel, line, config->accent);
    if (app->game.dirty)
        draw_text(number_x + number_width + pixel, number_y, pixel * 0.5f, "*", config->ink);
    draw_text(((float)width - draw_text_width(unit * 0.4f, "COUNT")) / 2, number_y + pixel * 6, unit * 0.4f, "COUNT",
              faint);

    /* What the game knows about its save. */
    float text_pixel = unit * 0.45f;
    draw_text(((float)width - draw_text_width(text_pixel, app->save_text)) / 2, unit * 33, text_pixel, app->save_text,
              app->save == SAVE_REFUSED || app->save == SAVE_DAMAGED ? (struct colour){0.92f, 0.45f, 0.40f}
                                                                     : config->ink);
    snprintf(line, sizeof line, "SAVES CONFIRMED: %lld", (long long)app->game.saves);
    draw_text(((float)width - draw_text_width(unit * 0.3f, line)) / 2, unit * 36.5f, unit * 0.3f, line, faint);

    /* Three lamps: content read, sound device, controller. */
    float lamps_y = unit * 41;
    lamp(margin, lamps_y, unit, app->content_ok, app->content_ok ? "CONTENT FROM THE CARD" : "NO CONTENT", app);
    lamp(margin + (float)width * 0.36f, lamps_y, unit, app->sound_ok, app->sound_ok ? "SOUND" : "NO SOUND DEVICE", app);
    snprintf(line, sizeof line, "%.24s", app->controller ? app->controller_name : "NO CONTROLLER");
    lamp(margin + (float)width * 0.60f, lamps_y, unit, app->controller != NULL, line, app);

    draw_text(margin, unit * 45.5f, unit * 0.3f, "UP/DOWN: CHANGE    ENTER OR A: SAVE    ESC OR B: QUIT", config->ink);
    if (app->config.lab_keys)
        draw_text(margin, unit * 48, unit * 0.22f, "LABORATORY KEYS: F ENDS WITH EXIT CODE 3, C ABORTS", faint);
    draw_text(margin, unit * 50.5f, unit * 0.22f, facts, faint);
    /* A bar that moves every frame: a still picture means a stalled game. */
    draw_rect((float)(frames % (unsigned long)width), (float)height - unit * 0.5f, unit * 3, unit * 0.5f, config->accent);
    draw_end();
}

/* ------------------------------------------------------------------ main */

int main(void)
{
    static struct app app;
    const char *headless_flag = getenv("MUN_HELLO_HEADLESS");
    const char *capture = getenv("MUN_HELLO_CAPTURE");
    int headless = headless_flag && strcmp(headless_flag, "1") == 0;
    const char *capture_at = getenv("MUN_HELLO_CAPTURE_FRAME");
    unsigned long capture_frame = capture_at ? strtoul(capture_at, NULL, 10) : 1;
    struct script script = {getenv("MUN_HELLO_SCRIPT"), 0};
    char path[1024], facts[200];

    config_defaults(&app.config);
    game_init(&app.game);
    if (mun_open(&app.job.session) != MUN_OK) {
        fprintf(stderr, "hello-mun: out of memory\n");
        return 2;
    }
    mun_session *session = app.job.session;
    const char *card = mun_get(session, MUN_FACT_CARD_ID), *version = mun_get(session, MUN_FACT_CONTENT_VERSION);
    const char *profile = mun_get(session, MUN_FACT_RUNTIME_PROFILE), *mode = mun_get(session, MUN_FACT_DISPLAY_MODE);
    fprintf(stderr, "hello-mun: %s; card %s, version %s, profile %s, session %s, display %s\n",
            mun_in_session(session) ? "in a MUN session" : "not in a MUN session", card ? card : "-",
            version ? version : "-", profile ? profile : "-",
            mun_get(session, MUN_FACT_SESSION) ? mun_get(session, MUN_FACT_SESSION) : "-", mode ? mode : "-");
    snprintf(facts, sizeof facts, "CARD %s  VERSION %s  PROFILE %s  DISPLAY %s", card ? card : "-",
             version ? version : "-", profile ? profile : "-", mode ? mode : "-");

    /* The card's content: the configuration, then the sound it names. */
    const char *blip = NULL;
    if (mun_content_path(session, "hello.cfg", path, sizeof path) == MUN_OK)
        app.content_ok = config_load(&app.config, path);
    fprintf(stderr, "hello-mun: content: %s\n",
            app.content_ok ? "hello.cfg read" : "no hello.cfg (built-in settings are used)");
    if (mun_content_path(session, "sounds/blip.wav", path, sizeof path) == MUN_OK)
        blip = path;

    restore(&app);

    if (SDL_Init(headless ? SDL_INIT_EVENTS | SDL_INIT_TIMER
                          : SDL_INIT_VIDEO | SDL_INIT_EVENTS | SDL_INIT_TIMER | SDL_INIT_GAMECONTROLLER) != 0) {
        fprintf(stderr, "hello-mun: SDL_Init: %s\n", SDL_GetError());
        return 2;
    }
    SDL_Window *window = NULL;
    SDL_GLContext gl = NULL;
    int width = 0, height = 0;
    if (!headless) {
        /* The window takes the display's own mode: the console has already
         * set the one the player chose (MUN_DISPLAY_MODE says which). */
        SDL_DisplayMode display;
        if (SDL_GetCurrentDisplayMode(0, &display) != 0 || display.w <= 0 || display.h <= 0) {
            fprintf(stderr, "hello-mun: no usable display mode: %s\n", SDL_GetError());
            SDL_Quit();
            return 2;
        }
        const char *windowed = getenv("MUN_HELLO_WINDOW"); /* "WxH": a window on a desktop, for a look */
        int w = display.w, h = display.h;
        Uint32 flags = SDL_WINDOW_OPENGL | SDL_WINDOW_FULLSCREEN;
        if (windowed && sscanf(windowed, "%dx%d", &w, &h) == 2 && w > 0 && h > 0)
            flags = SDL_WINDOW_OPENGL;
        SDL_GL_SetAttribute(SDL_GL_DOUBLEBUFFER, 1);
        window = SDL_CreateWindow("MUN Hello", SDL_WINDOWPOS_UNDEFINED, SDL_WINDOWPOS_UNDEFINED, w, h, flags);
        gl = window ? SDL_GL_CreateContext(window) : NULL;
        if (!gl || draw_init() != 0) {
            fprintf(stderr, "hello-mun: no OpenGL window: %s\n", SDL_GetError());
            SDL_Quit();
            return 2;
        }
        int vsync = SDL_GL_SetSwapInterval(1) == 0;
        SDL_GL_GetDrawableSize(window, &width, &height);
        SDL_ShowCursor(SDL_DISABLE);
        fprintf(stderr, "hello-mun: video %s, %dx%d @%d Hz, vsync %s, OpenGL %s on %s\n", SDL_GetCurrentVideoDriver(),
                width, height, display.refresh_rate, vsync ? "on" : "off", draw_version(), draw_renderer());
        app.sound_ok = sound_open(blip);
        fprintf(stderr, "hello-mun: sound: %s%s\n", app.sound_ok ? sound_device() : "no device",
                app.sound_ok && sound_blip_from_file() ? ", blip from the card's content" : "");
        sound_play(SOUND_BLIP); /* one sound at start: a silent television is a finding */
    }

    unsigned long frames = 0;
    Uint32 started = SDL_GetTicks();
    while (!app.quit) {
        Uint32 frame_start = SDL_GetTicks();
        SDL_Event event;
        while (SDL_PollEvent(&event))
            handle_event(&app, &event);
        poll_stick(&app);
        script_step(&app, &script);
        save_poll(&app);
        if (!headless) {
            draw_frame(&app, width, height, facts, frames);
            if (capture && frames == capture_frame) {
                int failed = draw_capture(capture, width, height);
                fprintf(stderr, "hello-mun: frame %s %s\n", failed ? "could not be written to" : "written to", capture);
            }
            SDL_GL_SwapWindow(window);
        }
        frames++;
        /* A game paces itself. Where the swap waits for the display, a frame
         * has already taken its time; where it does not (no window, no
         * vertical sync, a renderer that returns at once) the rest of a
         * sixtieth of a second is slept instead of drawing as fast as the
         * processor allows. */
        Uint32 spent = SDL_GetTicks() - frame_start;
        if (spent < 16)
            SDL_Delay(16 - spent);
    }

    /* A save still in flight is the console's to finish or fail: it answers
     * on its own schedule and the card keeps a loadable save either way.
     * Give the answer a moment, for the log, and leave. */
    for (int i = 0; app.job.running && i < 100; i++) {
        SDL_Delay(10);
        save_poll(&app);
    }
    float seconds = (float)(SDL_GetTicks() - started) / 1000.0f;
    fprintf(stderr, "hello-mun: exit %d after %lu frames in %.1f s; count %lld, saves confirmed %lld%s\n",
            app.exit_code, frames, (double)seconds, (long long)app.game.count, (long long)app.game.saves,
            app.job.running ? "; a save was still unanswered" : "");
    if (!headless) {
        sound_close();
        SDL_GL_DeleteContext(gl);
        SDL_DestroyWindow(window);
    }
    if (!app.job.running)
        mun_close(session); /* otherwise the save thread still holds it; the process is ending */
    SDL_Quit();
    return app.exit_code;
}
