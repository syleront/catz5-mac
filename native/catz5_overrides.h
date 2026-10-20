/* Catz 5 native replacements, included by the generated sources before they
 * define FN_<addr> (see game.toml [translate] native and funcs.h's override
 * note). One translated function is replaced here; see the comment on it. */
#ifndef CATZ5_OVERRIDES_H
#define CATZ5_OVERRIDES_H

/* Petz5Filmstrip::LoadBMP(char const *path, bool, bool, bool) - exe 004361b0.
 *
 * The translation is BMP-only: with the "8-bit filmstrip" flag clear it hands
 * the raw file buffer to FUN_00439c30, which reads a BITMAPFILEHEADER +
 * BITMAPINFOHEADER straight out of it (biWidth at +0x12, biHeight at +0x16,
 * biBitCount at +0x1c) and allocates biBitCount*biWidth*biHeight/8 bytes.
 *
 * The Playscene Editor's "Import Bitmap" page (template 253) passes the file
 * the player picked. For a .jpg those offsets are JPEG entropy data, so the
 * allocation is garbage - 110 MiB for a 640x480 JPEG, 488 MiB for 4000x3000 -
 * and the 256 MiB guest heap refuses it: the game shows its own "Out Of
 * Memory" box and calls ExitProcess.
 *
 * The game already carries a JPEG decoder: XOpenDIB (exe 0045fce0) checks the
 * extension (IsJPG) and, for a JPEG, runs its statically linked IJG libjpeg
 * into a 24-bit DIB XMemory (verified: a 640x480 JPEG decodes to
 * biWidth=640 biHeight=480 biBitCount=24). The override decodes through it,
 * writes the DIB out as a real 24-bit .bmp in the host temp directory (granted
 * to the guest), and runs the untouched translation on that path, so the
 * imported picture reaches the Editor exactly as a .bmp does.
 *
 * A note for whoever replaces this: a native body that calls FUN_00439c30
 * itself is easy to get subtly wrong. It must pop the four stack arguments
 * (RET 0x10), it must pass FUN_00439c30's arguments as the translation does
 * (param_3/param_4 = the call's arg3/arg4, which pick the 32-bit final buffer
 * over the 24-bit one), and every guest_call clobbers ECX, so `this` has to be
 * reloaded before any call that takes it. The temp-file route sidesteps all of
 * that by letting the translated body run unchanged. */
void catz5_fn_004361b0(X86 *c);
#define FN_004361b0 catz5_fn_004361b0

#endif /* CATZ5_OVERRIDES_H */
