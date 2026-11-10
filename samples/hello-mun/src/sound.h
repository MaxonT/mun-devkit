/* MUN Hello's sounds, through OpenAL as the GL profile provides it. A
 * machine without a sound device plays nothing and the game goes on. */
#ifndef HELLO_SOUND_H
#define HELLO_SOUND_H

enum sound_id { SOUND_BLIP = 0, SOUND_SAVED, SOUND_REFUSED, SOUND_COUNT_ };

/* Opens the default device. `blip_path` names a PCM 16-bit WAV to use for
 * SOUND_BLIP (the card's content); NULL or an unusable file gives a tone
 * made here instead. Returns 1 if a device opened, 0 otherwise. */
int sound_open(const char *blip_path);

/* 1 when SOUND_BLIP was read from `blip_path`. */
int sound_blip_from_file(void);

/* The device's own name, or "none". */
const char *sound_device(void);

void sound_play(enum sound_id id);
void sound_close(void);

#endif
