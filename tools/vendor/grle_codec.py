"""
Pure-Python GRLE encoder/decoder for FS25 info layers (1 image channel).
Reverse-engineered from GE-baked references (tools/grle_re_refs/). No GE needed.

Format:
  header 21 bytes: b"GRLE" + version(u8=1) + width,height,imageChannels,payloadSize (u32 LE each)
  payload: row-major run stream. Each run of (value V, count N):
    N == 1: single byte V
    N >= 2: V, V, 0xFF * k, term   where N = 255*k + term + 2, k=(N-2)//255, term=(N-2)%255 (0..254)
  Disambiguation: RLE has no adjacent same-value runs, so byte[i+1]==byte[i] => count>=2 form,
  otherwise a lone (count==1) pixel.
"""
import struct, numpy as np

def encode(arr, image_channels=1):
    """arr: 2D uint8, row-major (shape h,w). Returns grle bytes."""
    h, w = arr.shape
    flat = arr.ravel()
    payload = bytearray()
    n = len(flat); i = 0
    while i < n:
        v = int(flat[i]); j = i + 1
        while j < n and flat[j] == v:
            j += 1
        count = j - i
        if count == 1:
            payload.append(v)
        else:
            k, term = divmod(count - 2, 255)
            payload += bytes((v, v)) + b"\xff" * k + bytes((term,))
        i = j
    hdr = b"GRLE" + bytes((1,)) + struct.pack("<IIII", w, h, image_channels, len(payload))
    return hdr + bytes(payload)

def decode(data):
    """grle bytes -> (2D uint8 array, image_channels)."""
    assert data[:4] == b"GRLE", "not a GRLE file"
    w, h, ch, psize = struct.unpack_from("<IIII", data, 5)
    pl = data[21:21 + psize]
    out = np.empty(w * h, dtype=np.uint8)
    pos = 0; i = 0; L = len(pl)
    while i < L:
        v = pl[i]
        if i + 1 < L and pl[i + 1] == v:      # doubled value => count >= 2
            i += 2
            k = 0
            while i < L and pl[i] == 0xFF:
                k += 1; i += 1
            term = pl[i]; i += 1
            count = 255 * k + term + 2
        else:                                  # lone byte => count == 1
            i += 1
            count = 1
        out[pos:pos + count] = v
        pos += count
    assert pos == w * h, f"decoded {pos} px, expected {w*h}"
    return out.reshape(h, w), ch

if __name__ == "__main__":
    # Self-contained round-trip self-test: encode -> decode must reproduce the array, no external files.
    rng = np.random.default_rng(0)
    cases = {
        "all-zero":  np.zeros((64, 64), np.uint8),
        "all-one":   np.ones((128, 32), np.uint8),
        "all-255":   np.full((16, 256), 255, np.uint8),
        "rect":      np.pad(np.ones((10, 10), np.uint8), ((3, 3), (5, 5))),
        "few-ids":   rng.integers(0, 6, size=(100, 100)).astype(np.uint8),
        "sparse":    (rng.random((200, 200)) < 0.02).astype(np.uint8) * 12,
    }
    ok = True
    for name, arr in cases.items():
        rt, _ = decode(encode(arr))
        match = np.array_equal(rt, arr)
        print(f"{name:10s} {str(arr.shape):12s} round-trip={match}")
        ok = ok and match
    print("\nALL round-trips correct:", ok)
    raise SystemExit(0 if ok else 1)
