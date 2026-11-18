// Copyright 2026 Iván Moreno Mendoza
// SPDX-License-Identifier: MIT-0

/* Drawing for MUN Hello: flat rectangles and a 3x5 pixel typeface, on the
 * fixed-function OpenGL a GL-profile game gets through SDL2. Coordinates are
 * pixels, origin at the top left. One thread, the one that owns the context. */
#ifndef HELLO_DRAW_H
#define HELLO_DRAW_H

#include "config.h"

/* Finds the OpenGL entry points through SDL. Call once, with a current
 * context. Returns 0, or -1 after logging which entry point is missing. */
int draw_init(void);

/* The renderer's own name (GL_RENDERER), for the log. */
const char *draw_renderer(void);
const char *draw_version(void);

void draw_begin(int width, int height, struct colour background);
void draw_rect(float x, float y, float w, float h, struct colour colour);

/* Draws `text` (capitals, digits and common punctuation; lower case is
 * drawn as capitals, anything else as '?') with glyph pixels of `pixel`
 * screen pixels. Returns the width drawn. */
float draw_text(float x, float y, float pixel, const char *text, struct colour colour);
float draw_text_width(float pixel, const char *text);

/* Sends what was drawn to the context; the caller swaps the window. */
void draw_end(void);

/* Writes the frame just drawn (before the swap) as a binary PPM, for checks
 * that look at the picture. Returns 0 on success. */
int draw_capture(const char *path, int width, int height);

#endif
