"""
FS25 .i3d.shapes cipher (pure Python port of GIANTS' encryption).

Ported from the open-source I3DShapesTool (Donkie/I3DShapesTool, MIT) I3DCipher.cs. The .i3d.shapes
payload is encrypted with a ChaCha-style stream cipher whose initial 16-word state is looked up from a
big constant key table by an 8-bit seed (stored in the file header). Because it is an XOR keystream,
ENCRYPT == DECRYPT: the same process() call both decrypts a game file and encrypts one we author.

Header (4 bytes) for version >= 4 (FS25 is version 10):  [version, 0x00, seed, 0x00]  (little-endian shorts).

The KEY_CONST table (256 seeds x 16 uint32 = 4096 words) is loaded once from the vendored C# source so we
never hand-transcribe 4096 constants. Keep tools/vendor/I3DCipher.cs alongside this file.
"""
import os, struct

_HERE = os.path.dirname(os.path.abspath(__file__))
_CS = os.path.join(_HERE, "vendor", "I3DCipher.cs")

def _load_key_const(path=_CS):
    import re
    txt = open(path, encoding="utf-8-sig").read()
    # grab everything between  keyConst = {  ... };
    m = re.search(r"keyConst\s*=\s*\{(.*?)\}\s*;", txt, re.DOTALL)
    if not m:
        raise RuntimeError("keyConst array not found in " + path)
    vals = [int(t, 16) for t in re.findall(r"0x[0-9A-Fa-f]{1,8}", m.group(1))]
    if len(vals) != 4096:
        raise RuntimeError(f"expected 4096 key words, got {len(vals)}")
    return vals

KEY_CONST = _load_key_const()
MASK = 0xFFFFFFFF
BLOCK = 64  # bytes


def _rol(v, b): return ((v << b) | (v >> (32 - b))) & MASK
def _ror(v, b): return ((v >> b) | (v << (32 - b))) & MASK


def _shuffle1(k, a, b, c, d):
    k[c] ^= _rol((k[b] + k[a]) & MASK, 7)
    k[d] ^= _rol((k[c] + k[a]) & MASK, 9)
    k[b] ^= _rol((k[c] + k[d]) & MASK, 13)
    k[a] ^= _ror((k[b] + k[d]) & MASK, 14)


def _shuffle2(k, a, b, c, d):
    k[c] ^= _rol((k[b] + k[a]) & MASK, 7)
    k[d] ^= _rol((k[b] + k[c]) & MASK, 9)
    k[a] ^= _rol((k[c] + k[d]) & MASK, 13)
    k[b] ^= _ror((k[d] + k[a]) & MASK, 14)


def _process_blocks(buf, key):
    """buf: list[uint32] length%16==0; key: list[16] with key[8]/key[9]=block counter. In place."""
    counter = key[8] | (key[9] << 32)
    for i in range(0, len(buf), 16):
        tk = key[:]
        for _ in range(10):
            _shuffle1(tk, 0x0, 0xC, 0x4, 0x8)
            _shuffle1(tk, 0x5, 0x1, 0x9, 0xD)
            _shuffle1(tk, 0xA, 0x6, 0xE, 0x2)
            _shuffle1(tk, 0xF, 0xB, 0x3, 0x7)
            _shuffle2(tk, 0x3, 0x0, 0x1, 0x2)
            _shuffle2(tk, 0x4, 0x5, 0x6, 0x7)
            _shuffle1(tk, 0xA, 0x9, 0xB, 0x8)
            _shuffle2(tk, 0xE, 0xF, 0xC, 0xD)
        for j in range(16):
            buf[i + j] = (buf[i + j] ^ ((key[j] + tk[j]) & MASK)) & MASK
        counter = (counter + 1) & 0xFFFFFFFFFFFFFFFF
        key[8] = counter & MASK
        key[9] = (counter >> 32) & MASK


def process(data: bytes, seed: int, block_index: int = 0) -> bytes:
    """XOR-cipher `data` with the keystream for `seed`, starting at `block_index`. Symmetric."""
    key = [KEY_CONST[(seed << 4) + i] for i in range(16)]
    key[8] = block_index & MASK
    key[9] = (block_index >> 32) & MASK
    padlen = (-len(data)) % BLOCK
    copy = data + b"\x00" * padlen
    blocks = list(struct.unpack("<%dI" % (len(copy) // 4), copy))
    _process_blocks(blocks, key)
    out = struct.pack("<%dI" % len(blocks), *blocks)
    return out[:len(data)]


def read_header(data: bytes):
    """-> (version, seed, body_offset). Supports FS version>=4 layout (FS25 = v10)."""
    b1, b2, b3, b4 = data[0], data[1], data[2], data[3]
    if b1 >= 4:
        return b1, b3, 4
    if b4 in (2, 3):
        return b4, b2, 4
    raise NotImplementedError(f"unknown .shapes header {data[:4].hex()}")


def write_header(version: int, seed: int) -> bytes:
    if version >= 4:
        return bytes([version & 0xFF, 0, seed & 0xFF, 0])
    return bytes([0, seed & 0xFF, 0, version & 0xFF])


def decrypt_file(path: str):
    """-> (version, seed, plaintext_body_bytes)."""
    data = open(path, "rb").read()
    version, seed, off = read_header(data)
    return version, seed, process(data[off:], seed, 0)


def encrypt_file(path: str, version: int, seed: int, body: bytes):
    open(path, "wb").write(write_header(version, seed) + process(body, seed, 0))


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        sys.exit("usage: python shapes_cipher.py <path/to/file.i3d.shapes>")
    p = sys.argv[1]
    ver, seed, body = decrypt_file(p)
    print(f"version={ver} seed={seed} bodylen={len(body)}")
    n = struct.unpack_from("<i", body, 0)[0]
    print(f"entity count (int32[0]) = {n}")
    print("body[0:64] hex :", body[:64].hex())
    print("body[0:96] ascii:", "".join(chr(x) if 32 <= x < 127 else "." for x in body[:96]))
    # round-trip: re-encrypt body and compare to original file bytes
    reenc = write_header(ver, seed) + process(body, seed, 0)
    orig = open(p, "rb").read()
    print("ROUND-TRIP identical:", reenc == orig, f"({len(reenc)} vs {len(orig)} bytes)")
