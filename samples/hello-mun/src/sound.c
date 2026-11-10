#include "sound.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef __APPLE__
#include <OpenAL/al.h>
#include <OpenAL/alc.h>
#else
#include <AL/al.h>
#include <AL/alc.h>
#endif

#define RATE 48000
#define WAV_MAX_BYTES (1024 * 1024)

static ALCdevice *device;
static ALCcontext *context;
static ALuint buffers[SOUND_COUNT_], sources[SOUND_COUNT_];
static int blip_from_file;
static char device_name[96] = "none";

/* A tone that glides from `from` to `to` Hz, with a soft start and end. */
static void tone(ALuint buffer, double from, double to, double seconds, double peak)
{
    int frames = (int)(RATE * seconds);
    short *samples = malloc((size_t)frames * sizeof *samples);
    if (!samples)
        return;
    double phase = 0;
    for (int n = 0; n < frames; n++) {
        double t = (double)n / frames;
        double envelope = fmin(1.0, fmin(n / 240.0, (frames - n) / 960.0));
        phase += 2 * 3.14159265358979 * (from + (to - from) * t) / RATE;
        samples[n] = (short)(32767 * peak * envelope * sin(phase));
    }
    alBufferData(buffer, AL_FORMAT_MONO16, samples, (ALsizei)((size_t)frames * sizeof *samples), RATE);
    free(samples);
}

static unsigned le32(const unsigned char *p)
{
    return (unsigned)p[0] | (unsigned)p[1] << 8 | (unsigned)p[2] << 16 | (unsigned)p[3] << 24;
}

/* Loads a PCM 16-bit mono or stereo WAV. The file is the card's: every
 * length is checked against what was read. Returns 1 on success. */
static int load_wav(ALuint buffer, const char *path)
{
    FILE *file = fopen(path, "rb");
    if (!file)
        return 0;
    unsigned char *data = malloc(WAV_MAX_BYTES);
    size_t size = data ? fread(data, 1, WAV_MAX_BYTES, file) : 0;
    fclose(file);
    int ok = 0;
    unsigned channels = 0, rate = 0, bits = 0;
    if (data && size >= 12 && memcmp(data, "RIFF", 4) == 0 && memcmp(data + 8, "WAVE", 4) == 0) {
        size_t at = 12;
        while (at + 8 <= size) {
            unsigned length = le32(data + at + 4);
            const unsigned char *body = data + at + 8;
            if (length > size - at - 8)
                break;
            if (memcmp(data + at, "fmt ", 4) == 0 && length >= 16) {
                unsigned format = (unsigned)body[0] | (unsigned)body[1] << 8;
                channels = (unsigned)body[2] | (unsigned)body[3] << 8;
                rate = le32(body + 4);
                bits = (unsigned)body[14] | (unsigned)body[15] << 8;
                if (format != 1)
                    break;
            } else if (memcmp(data + at, "data", 4) == 0) {
                if (bits == 16 && (channels == 1 || channels == 2) && rate >= 8000 && rate <= 96000 && length >= 2) {
                    alBufferData(buffer, channels == 1 ? AL_FORMAT_MONO16 : AL_FORMAT_STEREO16, body,
                                 (ALsizei)(length & ~1u), (ALsizei)rate);
                    ok = alGetError() == AL_NO_ERROR;
                }
                break;
            }
            at += 8 + (size_t)length + (length & 1);
        }
    }
    free(data);
    return ok;
}

int sound_open(const char *blip_path)
{
    device = alcOpenDevice(NULL);
    if (!device)
        return 0;
    context = alcCreateContext(device, NULL);
    if (!context || !alcMakeContextCurrent(context)) {
        sound_close();
        return 0;
    }
    const ALCchar *name = alcGetString(device, ALC_DEVICE_SPECIFIER);
    snprintf(device_name, sizeof device_name, "%s", name ? name : "unnamed");
    alGenBuffers(SOUND_COUNT_, buffers);
    alGenSources(SOUND_COUNT_, sources);
    blip_from_file = blip_path && load_wav(buffers[SOUND_BLIP], blip_path);
    if (!blip_from_file)
        tone(buffers[SOUND_BLIP], 660, 660, 0.07, 0.5);
    tone(buffers[SOUND_SAVED], 660, 1320, 0.22, 0.5);
    tone(buffers[SOUND_REFUSED], 220, 160, 0.30, 0.5);
    for (int i = 0; i < SOUND_COUNT_; i++)
        alSourcei(sources[i], AL_BUFFER, (ALint)buffers[i]);
    return 1;
}

int sound_blip_from_file(void)
{
    return blip_from_file;
}

const char *sound_device(void)
{
    return device_name;
}

void sound_play(enum sound_id id)
{
    if (!context || id < 0 || id >= SOUND_COUNT_)
        return;
    alSourceStop(sources[id]);
    alSourcePlay(sources[id]);
}

void sound_close(void)
{
    if (context) {
        alDeleteSources(SOUND_COUNT_, sources);
        alDeleteBuffers(SOUND_COUNT_, buffers);
        alcMakeContextCurrent(NULL);
        alcDestroyContext(context);
        context = NULL;
    }
    if (device) {
        alcCloseDevice(device);
        device = NULL;
    }
}
