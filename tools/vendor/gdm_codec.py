"""
Pure-Python GDM (densityMap) codec for FS25, single-compression-range maps where
only the field-ground value varies (other channels 0) — covers densityMap_ground
for our field bake. Format reverse-engineered from GIANTS Editor's reader
(FUN_1404032d0) via alembic decompilation.

Layout:
  Header 16 bytes:
    [0:4]  signature 0x46444D22  ('"MDF', little-endian)
    [4:8]  version u32 = 0
    [8]    mapSizeLog   (map px = 1 << (v+5))   -> 9  => 16384
    [9]    chunkSizeLog (chunk px = 1 << v)     -> 5  => 32
    [10]   maxBpp                               -> 2
    [11]   numChannels                          -> 11
    [12]   numCompressionRanges                 -> 1
    [13]   numTypeIndexChannels                 -> 0
    [14],[15] = 0
  Then 512x512 chunk cells in row-major order, each:
    [numBitplanes u8][paletteCount u8][palette: paletteCount x u16 LE][numBitplanes x 128 bytes]
  Pixel value = palette[index], index = bit0 | (bit1<<1) from the bitplanes
  (each plane = 1024 bits, row-major within the 32x32 chunk, LSB-first per byte).
"""
import struct, numpy as np

SIG = 0x46444D22
CHUNK = 32

def _header(mapsize=16384):
    import math
    msl = int(math.log2(mapsize)) - 5
    return (struct.pack("<I", SIG) + struct.pack("<I", 0)
            + bytes((msl, 5, 2, 11, 1, 0, 0, 0)))

def encode(arr):
    """arr: 2D uint8 (h=w=mapsize), field-ground value per pixel. -> gdm bytes."""
    h, w = arr.shape
    assert h == w, "square only"
    nchunks = h // CHUNK
    out = bytearray(_header(h))
    for cr in range(nchunks):
        rows = arr[cr*CHUNK:(cr+1)*CHUNK]
        for cc in range(nchunks):
            block = rows[:, cc*CHUNK:(cc+1)*CHUNK]      # 32x32
            flat = block.reshape(-1)                     # row-major, 1024
            # distinct values in first-appearance order
            _, idx = np.unique(flat, return_index=True)
            palette = flat[np.sort(idx)]
            pcount = len(palette)
            if pcount == 1:
                out += bytes((0, 1)) + struct.pack("<H", int(palette[0]))
                continue
            nplanes = 1 if pcount <= 2 else 2            # maxBpp 2 -> up to 4 values
            # map each pixel to its palette index
            lut = {int(v): i for i, v in enumerate(palette)}
            index = np.array([lut[int(v)] for v in flat], dtype=np.uint8)
            out += bytes((nplanes, pcount))
            for v in palette:
                out += struct.pack("<H", int(v))
            for b in range(nplanes):
                bits = (index >> b) & 1
                out += np.packbits(bits, bitorder="little").tobytes()
    return bytes(out)

def decode(data):
    """gdm bytes -> 2D uint8 field-ground array."""
    assert struct.unpack_from("<I", data, 0)[0] == SIG, "bad signature"
    msl, csl, maxbpp, nch, nranges, ntic = data[8], data[9], data[10], data[11], data[12], data[13]
    mapsize = 1 << (msl + 5)
    nchunks = mapsize // CHUNK
    out = np.zeros((mapsize, mapsize), dtype=np.uint8)
    pos = 16
    for cr in range(nchunks):
        for cc in range(nchunks):
            nplanes = data[pos]; pcount = data[pos+1]; pos += 2
            palette = [struct.unpack_from("<H", data, pos + 2*i)[0] for i in range(pcount)]
            pos += 2 * pcount
            if nplanes == 0:
                out[cr*CHUNK:(cr+1)*CHUNK, cc*CHUNK:(cc+1)*CHUNK] = palette[0]
            else:
                index = np.zeros(CHUNK*CHUNK, dtype=np.uint8)
                for b in range(nplanes):
                    plane = np.frombuffer(data[pos:pos+128], dtype=np.uint8); pos += 128
                    bits = np.unpackbits(plane, bitorder="little")[:CHUNK*CHUNK]
                    index |= (bits << b)
                pal = np.array(palette, dtype=np.uint16)
                block = pal[index].astype(np.uint8).reshape(CHUNK, CHUNK)
                out[cr*CHUNK:(cr+1)*CHUNK, cc*CHUNK:(cc+1)*CHUNK] = block
    return out

if __name__ == "__main__":
    # Self-contained round-trip self-test: encode -> decode reproduces the array. Square, power-of-2 side,
    # <=4 distinct values per 32x32 chunk (maxBpp=2).
    rng = np.random.default_rng(0)
    cases = {
        "all-zero":  np.zeros((64, 64), np.uint8),
        "uniform-3": np.full((64, 64), 3, np.uint8),
        "two-vals":  (rng.random((128, 128)) < 0.3).astype(np.uint8),
        "four-vals": rng.integers(0, 4, size=(128, 128)).astype(np.uint8),
    }
    ok = True
    for name, arr in cases.items():
        match = np.array_equal(decode(encode(arr)), arr)
        print(f"{name:10s} {str(arr.shape):12s} round-trip={match}")
        ok = ok and match
    print("\nALL round-trips correct:", ok)
    raise SystemExit(0 if ok else 1)
