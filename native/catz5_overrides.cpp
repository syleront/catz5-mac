/* Catz 5 native replacements. See native/catz5_overrides.h for the rationale;
 * this file defines the one function that header redirects. */
#include "guest.h"

#include "memory.h"
#include "platform/os.h"
#include "win32.h"

#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <string>
#include <unistd.h>

/* The untouched translation of Petz5Filmstrip::LoadBMP(char const*, bool, bool,
 * bool). The override runs it for every non-JPEG path, and for a JPEG it runs
 * it on a temporary .bmp it has just written. */
extern "C" void fn_004361b0(X86 *c);

namespace {

char lower(char ch) {
    return (ch >= 'A' && ch <= 'Z') ? (char)(ch - 'A' + 'a') : ch;
}

bool ext_is_jpeg(const char *path) {
    const char *dot = nullptr;
    for (const char *p = path; *p; ++p)
        if (*p == '.')
            dot = p;
    if (!dot)
        return false;
    return (lower(dot[1]) == 'j' && lower(dot[2]) == 'p' && lower(dot[3]) == 'g' &&
            dot[4] == '\0') ||
           (lower(dot[1]) == 'j' && lower(dot[2]) == 'p' && lower(dot[3]) == 'e' &&
            lower(dot[4]) == 'g' && dot[5] == '\0');
}

/* Copy the guest C string at `p` into `out` (always NUL-terminated). */
void guest_str(uint32_t p, char *out, size_t cap) {
    size_t i = 0;
    if (cap == 0)
        return;
    for (; i + 1 < cap; ++i) {
        uint8_t ch = rd8(p + (uint32_t)i);
        out[i] = (char)ch;
        if (!ch)
            break;
    }
    out[i] = '\0';
}

/* `RET 0x10` - what the translated body ends with. The call site pushed four
 * arguments and a return address; a native replacement has to remove exactly
 * that many bytes or every local the caller reads afterwards is displaced. */
void ret16(X86 *c, uint32_t esp) {
    c->eip = rd32(esp);
    c->r[4] = esp + 20u;
}

/* XOpenDIB hands back a heap XMemory holding a 24-bit DIB. Release it through
 * its own virtual destructor and the game's delete, exactly as a caller that
 * owned it would. */
void xmemory_destroy(X86 *c, uint32_t mem) {
    c->r[1] = mem;
    guest_call(c, 0x442700u);          /* XMemory::XUnlock */
    c->r[1] = mem;
    guest_call(c, 0x441a60u);          /* ~XMemory (unlocks and GlobalFrees) */
    guest_call(c, 0x463fe0u, mem);     /* PetzDelete(mem) */
}

} // namespace

/* Petz5Filmstrip::LoadBMP(char const *path, bool, bool, bool).
 *
 * The translation is BMP-only: with the "8-bit filmstrip" flag clear it hands
 * the raw file buffer to FUN_00439c30, which reads a BITMAPFILEHEADER +
 * BITMAPINFOHEADER out of it (biWidth at +0x12, biHeight at +0x16, biBitCount
 * at +0x1c) and allocates bitcount*width*height/8. For a .jpg those offsets are
 * JPEG entropy data, so the request is garbage (110 MiB for 640x480, 488 MiB
 * for 4000x3000) and the guest heap refuses it - the game's "Out Of Memory"
 * box, then ExitProcess.
 *
 * The game already carries a JPEG decoder: XOpenDIB (0045fce0) checks the
 * extension and runs its statically linked IJG libjpeg into a 24-bit DIB. For
 * a .jpg/.jpeg this override decodes through it, writes the DIB out as a real
 * 24-bit .bmp into the host temp directory (granted to the guest), and then
 * runs the *untouched* translation on that path. Every downstream contract is
 * then the BMP path's own: the XMemory load, the SEH frame, the RET 0x10, the
 * FUN_00439c30 call and its arguments. Every other path delegates directly. */
extern "C" void catz5_fn_004361b0(X86 *c) {
    const uint32_t esp = c->r[4];
    const uint32_t path = rd32(esp + 4u);
    const uint32_t self = c->r[1];          /* ECX = this (Petz5Filmstrip) */

    if (!path) {
        fn_004361b0(c);
        return;
    }
    char text[260];
    guest_str(path, text, sizeof text);
    if (!ext_is_jpeg(text)) {
        fn_004361b0(c);
        return;
    }

    /* XOpenDIB(path, library_list, throw) -> XMemory* holding a 24-bit DIB
     * (BITMAPINFOHEADER + pixels). __cdecl, so guest_call pushes all args. */
    uint32_t mem = guest_call(c, 0x45fce0u, path, 0u, 0u);
    if (!mem) {
        c->r[0] &= 0xffffff00u;
        ret16(c, esp);
        return;
    }
    c->r[1] = mem;                          /* ECX = this for XLock */
    uint32_t dib = guest_call(c, 0x4426b0u, 0u, 0u); /* XMemory::XLock(mem,false,false) */
    if (!dib) {
        xmemory_destroy(c, mem);
        c->r[0] &= 0xffffff00u;
        ret16(c, esp);
        return;
    }

    uint32_t hdr = rd32(dib);               /* biSize, 40 for the decoder's DIB */
    uint32_t bitcount = rd32(dib + 0xeu) & 0xffffu;
    int32_t width = (int32_t)rd32(dib + 4u);
    int32_t height = (int32_t)rd32(dib + 8u);
    uint32_t rows = height < 0 ? (uint32_t)(-height) : (uint32_t)height;
    uint32_t palette = 0;
    if (bitcount <= 8) {
        uint32_t colors = rd32(dib + 0x20u); /* biClrUsed */
        if (!colors)
            colors = 1u << bitcount;
        palette = colors * 4u;
    }
    uint32_t stride = ((uint32_t)width * bitcount + 31u) / 32u * 4u;
    uint32_t pixels = stride * rows;
    uint32_t off = 14u + hdr + palette;
    uint32_t total = off + pixels;

    uint32_t file = heap_alloc(total, false, 16);
    if (!file) {
        xmemory_destroy(c, mem);
        c->r[0] &= 0xffffff00u;
        ret16(c, esp);
        return;
    }
    wr16(file + 0x0u, 0x4d42u);             /* bfType 'BM' */
    wr32(file + 0x2u, total);               /* bfSize */
    wr32(file + 0x6u, 0u);                  /* bfReserved1/2 */
    wr32(file + 0xau, off);                 /* bfOffBits */
    for (uint32_t i = 0; i < hdr + palette + pixels; ++i)
        wr8(file + 14u + i, rd8(dib + i));

    xmemory_destroy(c, mem);

    /* The temp .bmp has to be a path the guest can open. os_temp_dir() is not
     * under the game directory, so grant it first, then present the file to the
     * guest as its own Z: drive path. */
    static unsigned serial;
    char host_tmp[512];
    snprintf(host_tmp, sizeof host_tmp, "%s/catz5-import-%ld-%u.bmp", os_temp_dir(),
             (long)getpid(), serial++);
    FILE *fp = fopen(host_tmp, "wb");
    if (!fp) {
        os_mkdir(os_temp_dir());            /* TMPDIR may not exist yet */
        fp = fopen(host_tmp, "wb");
    }
    if (fp) {
        fwrite(gm_ptr(file), 1, total, fp);
        fclose(fp);
    }
    heap_free(file);
    if (!fp) {
        c->r[0] &= 0xffffff00u;
        ret16(c, esp);
        return;
    }

    std::string guest_tmp = win32_guest_path_for_host(host_tmp);
    uint32_t cap = (uint32_t)guest_tmp.size() + 1u;
    uint32_t pbuf = cap > 1u ? heap_alloc(cap, false, 16) : 0u;
    if (pbuf) {
        win32_grant_host_path(host_tmp);
        gm_put_str(pbuf, guest_tmp.c_str(), cap);
        wr32(esp + 4u, pbuf);               /* arg1 = the temp .bmp */
        c->r[1] = self;                     /* guest_call clobbered ECX */
        fn_004361b0(c);                     /* original flow; ends with RET 0x10 */
        heap_free(pbuf);
    } else {
        c->r[0] &= 0xffffff00u;
        ret16(c, esp);
    }
    remove(host_tmp);
}
