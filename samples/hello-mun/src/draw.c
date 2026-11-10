#include "draw.h"

#include <stdio.h>
#include <stdlib.h>

#include <SDL.h>
#include <SDL_opengl.h>

/* The entry points are looked up at run time, as SDL recommends, so the
 * program does not link against a GL library by name. */
static void (APIENTRY *p_glViewport)(GLint, GLint, GLsizei, GLsizei);
static void (APIENTRY *p_glClearColor)(GLfloat, GLfloat, GLfloat, GLfloat);
static void (APIENTRY *p_glClear)(GLbitfield);
static void (APIENTRY *p_glMatrixMode)(GLenum);
static void (APIENTRY *p_glLoadIdentity)(void);
static void (APIENTRY *p_glOrtho)(GLdouble, GLdouble, GLdouble, GLdouble, GLdouble, GLdouble);
static void (APIENTRY *p_glColor4f)(GLfloat, GLfloat, GLfloat, GLfloat);
static void (APIENTRY *p_glEnableClientState)(GLenum);
static void (APIENTRY *p_glVertexPointer)(GLint, GLenum, GLsizei, const GLvoid *);
static void (APIENTRY *p_glDrawArrays)(GLenum, GLint, GLsizei);
static const GLubyte *(APIENTRY *p_glGetString)(GLenum);
static void (APIENTRY *p_glReadPixels)(GLint, GLint, GLsizei, GLsizei, GLenum, GLenum, GLvoid *);
static void (APIENTRY *p_glPixelStorei)(GLenum, GLint);

#define LOAD(name) \
    do { \
        *(void **)&p_##name = SDL_GL_GetProcAddress(#name); \
        if (!p_##name) { \
            fprintf(stderr, "hello-mun: OpenGL has no %s\n", #name); \
            return -1; \
        } \
    } while (0)

int draw_init(void)
{
    LOAD(glViewport);
    LOAD(glClearColor);
    LOAD(glClear);
    LOAD(glMatrixMode);
    LOAD(glLoadIdentity);
    LOAD(glOrtho);
    LOAD(glColor4f);
    LOAD(glEnableClientState);
    LOAD(glVertexPointer);
    LOAD(glDrawArrays);
    LOAD(glGetString);
    LOAD(glReadPixels);
    LOAD(glPixelStorei);
    return 0;
}

const char *draw_renderer(void)
{
    const GLubyte *name = p_glGetString(GL_RENDERER);
    return name ? (const char *)name : "unknown";
}

const char *draw_version(void)
{
    const GLubyte *name = p_glGetString(GL_VERSION);
    return name ? (const char *)name : "unknown";
}

/* Rectangles of one colour are gathered and sent together. */
#define BATCH_RECTS 1024
static GLfloat batch[BATCH_RECTS * 12];
static int batched;
static struct colour batch_colour;

static void flush(void)
{
    if (!batched)
        return;
    p_glColor4f(batch_colour.r, batch_colour.g, batch_colour.b, 1.0f);
    p_glVertexPointer(2, GL_FLOAT, 0, batch);
    p_glDrawArrays(GL_TRIANGLES, 0, batched * 6);
    batched = 0;
}

void draw_begin(int width, int height, struct colour background)
{
    p_glViewport(0, 0, width, height);
    p_glMatrixMode(GL_PROJECTION);
    p_glLoadIdentity();
    p_glOrtho(0, width, height, 0, -1, 1);
    p_glMatrixMode(GL_MODELVIEW);
    p_glLoadIdentity();
    p_glClearColor(background.r, background.g, background.b, 1.0f);
    p_glClear(GL_COLOR_BUFFER_BIT);
    p_glEnableClientState(GL_VERTEX_ARRAY);
    batched = 0;
}

void draw_rect(float x, float y, float w, float h, struct colour colour)
{
    if (batched == BATCH_RECTS || (batched && (colour.r != batch_colour.r || colour.g != batch_colour.g ||
                                               colour.b != batch_colour.b)))
        flush();
    batch_colour = colour;
    GLfloat *v = batch + batched * 12;
    v[0] = x;      v[1] = y;
    v[2] = x + w;  v[3] = y;
    v[4] = x + w;  v[5] = y + h;
    v[6] = x;      v[7] = y;
    v[8] = x + w;  v[9] = y + h;
    v[10] = x;     v[11] = y + h;
    batched++;
}

void draw_end(void)
{
    flush();
}

/* A 3x5 typeface: five rows of three pixels per glyph, top row first, the
 * left pixel the high bit. ASCII 32 to 95. */
#define G(a, b, c, d, e) (unsigned short)((a) << 12 | (b) << 9 | (c) << 6 | (d) << 3 | (e))
static const unsigned short GLYPHS[64] = {
    G(0, 0, 0, 0, 0), /* space */
    G(2, 2, 2, 0, 2), /* ! */
    G(5, 5, 0, 0, 0), /* " */
    G(5, 7, 5, 7, 5), /* # */
    G(3, 6, 2, 3, 6), /* $ */
    G(5, 1, 2, 4, 5), /* % */
    G(2, 5, 2, 5, 3), /* & */
    G(2, 2, 0, 0, 0), /* ' */
    G(1, 2, 2, 2, 1), /* ( */
    G(4, 2, 2, 2, 4), /* ) */
    G(0, 5, 2, 5, 0), /* * */
    G(0, 2, 7, 2, 0), /* + */
    G(0, 0, 0, 2, 4), /* , */
    G(0, 0, 7, 0, 0), /* - */
    G(0, 0, 0, 0, 2), /* . */
    G(1, 1, 2, 4, 4), /* / */
    G(7, 5, 5, 5, 7), /* 0 */
    G(2, 6, 2, 2, 7), /* 1 */
    G(6, 1, 2, 4, 7), /* 2 */
    G(6, 1, 2, 1, 6), /* 3 */
    G(5, 5, 7, 1, 1), /* 4 */
    G(7, 4, 6, 1, 6), /* 5 */
    G(3, 4, 7, 5, 7), /* 6 */
    G(7, 1, 2, 2, 2), /* 7 */
    G(7, 5, 7, 5, 7), /* 8 */
    G(7, 5, 7, 1, 6), /* 9 */
    G(0, 2, 0, 2, 0), /* : */
    G(0, 2, 0, 2, 4), /* ; */
    G(1, 2, 4, 2, 1), /* < */
    G(0, 7, 0, 7, 0), /* = */
    G(4, 2, 1, 2, 4), /* > */
    G(6, 1, 2, 0, 2), /* ? */
    G(2, 5, 7, 4, 3), /* @ */
    G(2, 5, 7, 5, 5), /* A */
    G(6, 5, 6, 5, 6), /* B */
    G(3, 4, 4, 4, 3), /* C */
    G(6, 5, 5, 5, 6), /* D */
    G(7, 4, 6, 4, 7), /* E */
    G(7, 4, 6, 4, 4), /* F */
    G(3, 4, 5, 5, 3), /* G */
    G(5, 5, 7, 5, 5), /* H */
    G(7, 2, 2, 2, 7), /* I */
    G(1, 1, 1, 5, 2), /* J */
    G(5, 5, 6, 5, 5), /* K */
    G(4, 4, 4, 4, 7), /* L */
    G(5, 7, 7, 5, 5), /* M */
    G(6, 5, 5, 5, 5), /* N */
    G(2, 5, 5, 5, 2), /* O */
    G(6, 5, 6, 4, 4), /* P */
    G(2, 5, 5, 7, 3), /* Q */
    G(6, 5, 6, 5, 5), /* R */
    G(3, 4, 2, 1, 6), /* S */
    G(7, 2, 2, 2, 2), /* T */
    G(5, 5, 5, 5, 7), /* U */
    G(5, 5, 5, 5, 2), /* V */
    G(5, 5, 7, 7, 5), /* W */
    G(5, 5, 2, 5, 5), /* X */
    G(5, 5, 2, 2, 2), /* Y */
    G(7, 1, 2, 4, 7), /* Z */
    G(3, 2, 2, 2, 3), /* [ */
    G(4, 4, 2, 1, 1), /* \ */
    G(6, 2, 2, 2, 6), /* ] */
    G(2, 5, 0, 0, 0), /* ^ */
    G(0, 0, 0, 0, 7), /* _ */
};

static unsigned short glyph(char c)
{
    if (c >= 'a' && c <= 'z')
        c = (char)(c - 'a' + 'A');
    if (c < 32 || c > 95)
        c = '?';
    return GLYPHS[c - 32];
}

float draw_text_width(float pixel, const char *text)
{
    float width = 0;
    for (const char *p = text; *p; p++)
        width += 4 * pixel;
    return width > 0 ? width - pixel : 0;
}

float draw_text(float x, float y, float pixel, const char *text, struct colour colour)
{
    float at = x;
    for (const char *p = text; *p; p++) {
        unsigned short bits = glyph(*p);
        for (int row = 0; row < 5; row++)
            for (int column = 0; column < 3; column++)
                if (bits >> (14 - (row * 3 + column)) & 1)
                    draw_rect(at + (float)column * pixel, y + (float)row * pixel, pixel, pixel, colour);
        at += 4 * pixel;
    }
    return at - x;
}

int draw_capture(const char *path, int width, int height)
{
    unsigned char *pixels = malloc((size_t)width * (size_t)height * 3);
    if (!pixels)
        return -1;
    flush();
    p_glPixelStorei(GL_PACK_ALIGNMENT, 1);
    p_glReadPixels(0, 0, width, height, GL_RGB, GL_UNSIGNED_BYTE, pixels);
    FILE *file = fopen(path, "wb");
    if (!file) {
        free(pixels);
        return -1;
    }
    fprintf(file, "P6\n%d %d\n255\n", width, height);
    for (int row = height - 1; row >= 0; row--) /* GL's first row is the bottom one */
        fwrite(pixels + (size_t)row * (size_t)width * 3, 1, (size_t)width * 3, file);
    int failed = ferror(file);
    failed |= fclose(file) != 0;
    free(pixels);
    return failed ? -1 : 0;
}
