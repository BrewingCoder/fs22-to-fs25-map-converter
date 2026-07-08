"""
FS25 .i3d.shapes codec (pure Python) - decode/inspect. Port of I3DShapesTool (Donkie/I3DShapesTool, MIT).

File = 4-byte header + encrypted payload (see gt_shapes_cipher). The cipher advances its 64-byte block
counter once per stream read/write CALL (ceil(nbytes/64) blocks), NOT continuously. In practice the only
cipher-level reads are the ENTITY FRAMING:
    int32 entityCount
    per entity: int32 type, int32 size, byte[size] data
So: count uses block 0; then each entity's type(4B)/size(4B)/data(size B) each advance the block counter.
The entity `data` blob is decrypted as ONE contiguous read, and the shape structure is parsed from that
plaintext with 4-byte alignment RELATIVE TO the blob start (a plain in-memory reader, no cipher).

Shape data layout (I3DPart + I3DShape, fileVersion 10):
  header:  int32 nameLen, ascii[nameLen], align(4), uint32 id(=shapeId)
  content: vec4 boundingVolume(cx,cy,cz,r); u32 cornerCount(=numTris*3); u32 numSubsets; u32 vertexCount;
           u32 options; subsets[numSubsets]; triangles[cornerCount/3] (u16 idx if vtx<=65536 else u32,
           STORED 0-based -> tool adds 1); align(4); positions[vtx](3 float); [normals]; [tangents v>=5];
           [uv0..uv3]; [vertexColor]; [skin]; [generic]; u32 numAttachments; attachments[]
  options bits: 1 Normals, 2 UV1, 4 UV2, 8 UV3, 0x10 UV4, 0x20 VertexColor, 0x40 Skin, 0x80 Tangents,
                0x100 SingleBlendWeights, 0x200 Generic
  subset: u32 firstVertex, numVertices, firstIndex, numIndices; if v>=6: one float UVDensity per present UV set
"""
import struct, math
from shapes_cipher import process, read_header, write_header, BLOCK

OPT_NORMALS, OPT_UV1, OPT_UV2, OPT_UV3, OPT_UV4 = 1, 2, 4, 8, 0x10
OPT_VCOLOR, OPT_SKIN, OPT_TANGENTS, OPT_SINGLEBLEND, OPT_GENERIC = 0x20, 0x40, 0x80, 0x100, 0x200
OPT_ALL = 0x3FF


def _ceil_blocks(n): return (n + BLOCK - 1) // BLOCK


class CipherReader:
    """Reads the encrypted payload the way CipherStream does: block counter advances per read() call."""
    def __init__(self, enc: bytes, seed: int):
        self.enc = enc; self.seed = seed; self.pos = 0; self.block = 0
    def read(self, n: int) -> bytes:
        chunk = self.enc[self.pos:self.pos + n]
        if len(chunk) != n:
            raise EOFError(f"want {n} at {self.pos}, have {len(chunk)}")
        out = process(chunk, self.seed, self.block)
        self.pos += n
        self.block += _ceil_blocks(n)
        return out
    def i32(self): return struct.unpack("<i", self.read(4))[0]
    def at_end(self): return self.pos >= len(self.enc)


class Blob:
    """Plain in-memory reader over a decrypted entity-data blob. Align is relative to blob start."""
    def __init__(self, data: bytes): self.d = data; self.p = 0
    def take(self, n):
        b = self.d[self.p:self.p + n]
        if len(b) != n: raise EOFError(f"blob want {n} at {self.p}/{len(self.d)}")
        self.p += n; return b
    def u32(self): return struct.unpack("<I", self.take(4))[0]
    def i32(self): return struct.unpack("<i", self.take(4))[0]
    def u16(self): return struct.unpack("<H", self.take(2))[0]
    def f32(self): return struct.unpack("<f", self.take(4))[0]
    def align(self, m=4):
        mod = self.p % m
        if mod: self.take(m - mod)
    def done(self): return self.p == len(self.d)


def decode_entities(path):
    """-> (version, seed, [ (type, data_bytes), ... ])"""
    raw = open(path, "rb").read()
    version, seed, off = read_header(raw)
    r = CipherReader(raw[off:], seed)
    n = r.i32()
    if n < 0 or n > 1_000_000:
        raise ValueError(f"bad entity count {n} (cipher/seed wrong?)")
    ents = []
    for _ in range(n):
        etype = r.i32()
        size = r.i32()
        data = r.read(size)
        ents.append((etype, data))
    return version, seed, ents


def parse_shape(data: bytes, version: int) -> dict:
    b = Blob(data)
    name_len = b.i32()
    name = b.take(name_len).decode("ascii", "replace")
    b.align(4)
    shape_id = b.u32()
    bv = (b.f32(), b.f32(), b.f32(), b.f32())
    corner_count = b.u32()
    num_subsets = b.u32()
    vertex_count = b.u32()
    options = b.u32()
    # FS25 (v10) subset = 28 bytes: uvDensity(f32), firstVertex, numVertices, firstIndex, numIndices,
    # + 2 reserved u32 (observed 0). (I3DShapesTool's v<=7 order was 4 counts then densities; v10 differs.)
    subsets = []
    for _ in range(num_subsets):
        s = {"uvDensity": b.f32(), "firstVertex": b.u32(), "numVertices": b.u32(),
             "firstIndex": b.u32(), "numIndices": b.u32(), "reserved0": b.u32(), "reserved1": b.u32()}
        subsets.append(s)
    is_int = vertex_count > 65536
    tris = []
    for _ in range(corner_count // 3):
        if is_int:
            tris.append((b.u32(), b.u32(), b.u32()))
        else:
            tris.append((b.u16(), b.u16(), b.u16()))
    b.align(4)
    positions = [(b.f32(), b.f32(), b.f32()) for _ in range(vertex_count)]
    normals = [(b.f32(), b.f32(), b.f32()) for _ in range(vertex_count)] if options & OPT_NORMALS else None
    tangents = None
    if options & OPT_TANGENTS and version >= 5:
        tangents = [(b.f32(), b.f32(), b.f32(), b.f32()) for _ in range(vertex_count)]
    uvsets = [None, None, None, None]
    for i, bit in enumerate((OPT_UV1, OPT_UV2, OPT_UV3, OPT_UV4)):
        if options & bit:
            if 4 <= version <= 5:
                uvsets[i] = [(lambda v=b.f32(), u=b.f32(): (u, v))() for _ in range(vertex_count)]
            else:
                uvsets[i] = [(b.f32(), b.f32()) for _ in range(vertex_count)]
    vcolor = [(b.f32(), b.f32(), b.f32(), b.f32()) for _ in range(vertex_count)] if options & OPT_VCOLOR else None
    if options & OPT_SKIN:
        single = bool(options & OPT_SINGLEBLEND)
        if not single:
            for _ in range(vertex_count * 4): b.f32()
        for _ in range(vertex_count * (1 if single else 4)): b.take(1)
    generic = [b.f32() for _ in range(vertex_count)] if options & OPT_GENERIC else None
    num_att = b.u32()
    attachments = []
    for _ in range(num_att):
        flags = b.u32()
        floats = [b.f32(), b.f32(), b.f32()] if flags & 4 else None
        nb = b.i32()
        adata = b.take(nb)
        attachments.append({"flags": flags, "floats": floats, "data": adata})
    consumed_all = b.done()
    return {
        "name": name, "shapeId": shape_id, "boundingVolume": bv,
        "cornerCount": corner_count, "numSubsets": num_subsets, "vertexCount": vertex_count,
        "options": options, "subsets": subsets, "numTris": len(tris), "triangles": tris,
        "positions": positions, "normals": normals, "tangents": tangents, "uvsets": uvsets,
        "vertexColor": vcolor, "generic": generic, "attachments": attachments,
        "consumed_all": consumed_all, "blob_pos": b.p, "blob_len": len(data),
    }


class BlobWriter:
    def __init__(self): self.buf = bytearray()
    def raw(self, b): self.buf += b
    def u32(self, v): self.buf += struct.pack("<I", v & 0xFFFFFFFF)
    def i32(self, v): self.buf += struct.pack("<i", v)
    def u16(self, v): self.buf += struct.pack("<H", v & 0xFFFF)
    def f32(self, v): self.buf += struct.pack("<f", v)
    def align(self, m=4):
        while len(self.buf) % m: self.buf += b"\x00"


def serialize_shape(sh: dict, version: int) -> bytes:
    """Inverse of parse_shape -> the plaintext entity-data blob (byte-exact)."""
    w = BlobWriter()
    name = sh["name"].encode("ascii")
    w.i32(len(name)); w.raw(name); w.align(4)
    w.u32(sh["shapeId"])
    for v in sh["boundingVolume"]:
        w.f32(v)
    w.u32(sh["cornerCount"]); w.u32(sh["numSubsets"]); w.u32(sh["vertexCount"]); w.u32(sh["options"])
    for s in sh["subsets"]:
        w.f32(s["uvDensity"]); w.u32(s["firstVertex"]); w.u32(s["numVertices"])
        w.u32(s["firstIndex"]); w.u32(s["numIndices"]); w.u32(s["reserved0"]); w.u32(s["reserved1"])
    is_int = sh["vertexCount"] > 65536
    for tri in sh["triangles"]:
        for i in tri:
            (w.u32 if is_int else w.u16)(i)
    w.align(4)
    for p in sh["positions"]:
        w.f32(p[0]); w.f32(p[1]); w.f32(p[2])
    if sh.get("normals"):
        for n in sh["normals"]:
            w.f32(n[0]); w.f32(n[1]); w.f32(n[2])
    if sh.get("tangents"):
        for t in sh["tangents"]:
            w.f32(t[0]); w.f32(t[1]); w.f32(t[2]); w.f32(t[3])
    for i, uv in enumerate(sh["uvsets"]):
        if uv is not None:
            for u in uv:
                if 4 <= version <= 5: w.f32(u[1]); w.f32(u[0])
                else: w.f32(u[0]); w.f32(u[1])
    if sh.get("vertexColor"):
        for c in sh["vertexColor"]:
            w.f32(c[0]); w.f32(c[1]); w.f32(c[2]); w.f32(c[3])
    if sh.get("generic"):
        for g in sh["generic"]:
            w.f32(g)
    w.u32(len(sh["attachments"]))
    for a in sh["attachments"]:
        w.u32(a["flags"])
        if a["floats"] is not None:
            for f in a["floats"]:
                w.f32(f)
        w.i32(len(a["data"])); w.raw(a["data"])
    return bytes(w.buf)


def encode_entities(version: int, seed: int, entities) -> bytes:
    """entities: list of (type, data_bytes). -> full file bytes (header + cipher framing)."""
    body = bytearray()
    block = 0

    def emit(b):
        nonlocal block
        body.extend(process(b, seed, block))
        block += _ceil_blocks(len(b))

    emit(struct.pack("<i", len(entities)))
    for etype, data in entities:
        emit(struct.pack("<i", etype))
        emit(struct.pack("<i", len(data)))
        emit(data)
    return write_header(version, seed) + bytes(body)


def opt_str(o):
    names = [(OPT_NORMALS, "N"), (OPT_UV1, "UV1"), (OPT_UV2, "UV2"), (OPT_UV3, "UV3"), (OPT_UV4, "UV4"),
             (OPT_VCOLOR, "VCOL"), (OPT_SKIN, "SKIN"), (OPT_TANGENTS, "TAN"), (OPT_GENERIC, "GEN")]
    s = "|".join(n for bit, n in names if o & bit)
    hi = o & ~OPT_ALL
    return s + (f"+hi{hi:#x}" if hi else "")


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        sys.exit("usage: python shapes_codec.py <path/to/file.i3d.shapes>")
    p = sys.argv[1]
    version, seed, ents = decode_entities(p)
    print(f"version={version} seed={seed} entities={len(ents)}")
    for i, (etype, data) in enumerate(ents):
        if etype != 1:
            print(f"  [{i}] type={etype} (non-shape) size={len(data)}"); continue
        try:
            sh = parse_shape(data, version)
            bv = sh["boundingVolume"]
            print(f"  [{i}] '{sh['name']}' id={sh['shapeId']} vtx={sh['vertexCount']} "
                  f"tris={sh['numTris']} subsets={sh['numSubsets']} opts=[{opt_str(sh['options'])}] "
                  f"bv=({bv[0]:.2f},{bv[1]:.2f},{bv[2]:.2f},r{bv[3]:.2f}) "
                  f"consumed={sh['consumed_all']} ({sh['blob_pos']}/{sh['blob_len']})")
        except Exception as e:
            print(f"  [{i}] PARSE FAIL size={len(data)}: {e}")
