"""
From-scratch binary generators for FS25 map data. NOTHING is copied or templated from another map - every byte
here is emitted from the GIANTS binary formats we understand (see docs/40_dem.md, 41_weights.md, 50_gdm.md,
51_grle.md). Each function builds the header + payload from first principles.
"""
import struct, math
import numpy as np
from PIL import Image


# ---- DEM: 16-bit grayscale heightmap (docs/40_dem.md) --------------------------------------------------------
def flat_dem(path, size_px, value, undulation=300, wavelength_px=96):
    """Near-flat heightmap. World height ~= value/65535 * heightScale (heightScale is on the TerrainTransformGroup).
    size_px for an 8192 m map @ unitsPerPixel=2 is 8192/2 + 1 = 4097 (power-of-two + 1, so patch edges align).

    IMPORTANT: a PERFECTLY flat DEM (one value everywhere) is degenerate - the terrain geometry quadtree collapses
    its coplanar LOD levels and emits 'Topology: Mesh contains non-manifold edges', then the physics mesh build
    CRASHES the load (verified: our flat map crashed with 16 such warnings; the working Sample map had 0). So we
    add an imperceptible smooth undulation (default ~+-1.1 m over ~192 m) giving every LOD level real, non-coplanar
    geometry. Pass undulation=0 for a truly flat (crash-prone) DEM."""
    if undulation:
        yy, xx = np.mgrid[0:size_px, 0:size_px].astype(np.float64)
        w = 2 * np.pi / wavelength_px
        arr = np.clip(value + (np.sin(xx * w) + np.cos(yy * w)) * (undulation / 2), 0, 65535).astype(np.uint16)
    else:
        arr = np.full((size_px, size_px), value, np.uint16)
    Image.fromarray(arr, mode="I;16").save(path)


# ---- Weights: 8-bit grayscale ground-layer weight (docs/41_weights.md) ---------------------------------------
def uniform_weight(path, size_px, value):
    """One ground layer's weight. 0 = layer absent at that pixel, 255 = layer fully present. Uniform grass =
    the grass layer at 255 everywhere, every other layer at 0."""
    Image.fromarray(np.full((size_px, size_px), value, np.uint8)).save(path)


# ---- GDM: GIANTS "MDF density map (docs/50_gdm.md) -----------------------------------------------------------
GDM_SIG = 0x46444D22   # '"MDF' little-endian
def blank_gdm(path, mapsize, num_channels, num_ranges, num_type_index_channels, range_splits=()):
    """A density map with every pixel = 0. Header then (mapsize/32)^2 chunks in row-major order; a blank chunk is
    one empty record PER compression range: [numBitplanes=0, paletteCount=1, palette[0]=0x0000].
      byte 8  mapSizeLog   : mapsize = 1 << (v+5)      (16384 -> 9)
      byte 9  chunkSizeLog  : chunk px = 1 << v         (32 -> 5)
      byte 10 maxBpp        : 2
      byte 11 numChannels   : total bit-channels packed per pixel (must match the terrain layer that reads it)
      byte 12 numCompressionRanges : how many value-groups the channels split into (1 for a plain map)
      byte 13 numTypeIndexChannels : low channels that are a type INDEX (e.g. fruits = 6); 0 otherwise
      byte 16.. : for a MULTI-range map, the (num_ranges-1) INTERIOR channel boundaries, one byte each. A 12-ch
                  density split 0-5 / 6-11 (compressionChannels=6) needs range_splits=(6,) => byte16=0x06.
                  OMITTING these = the engine reads boundary 0 -> "GDM file has wrong compression channel 0
                  (0 should be 6)" then a crash. Single-range maps write no split bytes.
    """
    assert len(range_splits) == num_ranges - 1, "need (num_ranges-1) interior split channels"
    msl = int(math.log2(mapsize)) - 5
    hdr = struct.pack("<I", GDM_SIG) + struct.pack("<I", 0) + bytes(
        (msl, 5, 2, num_channels, num_ranges, num_type_index_channels, 0, 0)) + bytes(range_splits)
    blank_chunk = bytes((0, 1, 0, 0)) * num_ranges          # empty record per range
    n_chunks = (mapsize // 32) ** 2
    open(path, "wb").write(hdr + blank_chunk * n_chunks)


def _enc_gdm_record(rv, width):
    """Encode one 32x32 range-value block. Uniform -> [numBitplanes=0, paletteCount=1, value]; mixed -> bit-planes.
    maxBpp=2 so a block may hold up to 4 distinct values (a single crop in a field = {0, cropVal} = 2)."""
    flat = rv.reshape(-1).astype(np.uint16)              # 1024 values
    _, first = np.unique(flat, return_index=True)
    palette = flat[np.sort(first)]                        # distinct, first-appearance order
    pc = len(palette)
    if pc == 1:
        return bytes((0, 1)) + struct.pack("<H", int(palette[0]))
    nplanes = 1 if pc <= 2 else 2
    lut = {int(v): i for i, v in enumerate(palette)}
    index = np.array([lut[int(v)] for v in flat], dtype=np.uint8)
    out = bytes((nplanes, pc)) + b"".join(struct.pack("<H", int(v)) for v in palette)
    for b in range(nplanes):
        out += np.packbits((index >> b) & 1, bitorder="little").tobytes()
    return out


def paint_gdm(path, mapsize, values, num_channels, num_ranges, num_type_index_channels, range_splits=()):
    """Like blank_gdm but encodes an actual mapsize x mapsize uint16 array (per-pixel PACKED value; for fruits =
    typeIdx | (state << numTypeIndexChannels)). From scratch, no template. Header identical to blank_gdm; then each
    32x32 chunk holds one record per compression range. Fast-paths uniform chunks (all-0 outside a field)."""
    assert len(range_splits) == num_ranges - 1
    splits = [0] + list(range_splits) + [num_channels]
    widths = [splits[r + 1] - splits[r] for r in range(num_ranges)]
    masks = [(1 << w) - 1 for w in widths]
    msl = int(math.log2(mapsize)) - 5
    out = bytearray(struct.pack("<I", GDM_SIG) + struct.pack("<I", 0)
                    + bytes((msl, 5, 2, num_channels, num_ranges, num_type_index_channels, 0, 0)) + bytes(range_splits))
    v = values.astype(np.uint32)
    C = 32; n = mapsize // C
    for cr in range(n):
        rows = v[cr * C:(cr + 1) * C]
        for cc in range(n):
            block = rows[:, cc * C:(cc + 1) * C]
            for r in range(num_ranges):
                rv = (block >> splits[r]) & masks[r]
                if rv.min() == rv.max():                  # uniform range value - fast path (most chunks)
                    out += bytes((0, 1)) + struct.pack("<H", int(rv.flat[0]))
                else:
                    out += _enc_gdm_record(rv, widths[r])
    open(path, "wb").write(bytes(out))


# ---- GRLE: GIANTS run-length info layer (docs/51_grle.md) ----------------------------------------------------
def blank_grle(path, size_px, image_channels=1, value=0):
    """An info layer with every pixel = value (default 0 = blank). Header(21B) + a single run over w*h pixels. A
    run of count N>=2 is encoded  V, V, 0xFF*k, term  where N = 255*k + term + 2. Use value=1 to paint a whole
    layer (e.g. the farmland info layer = all farmland 1, so that farmland's definition has pixels)."""
    n = size_px * size_px
    k, term = divmod(n - 2, 255)
    payload = bytes((value, value)) + b"\xff" * k + bytes((term,))
    hdr = b"GRLE" + bytes((1,)) + struct.pack("<IIII", size_px, size_px, image_channels, len(payload))
    open(path, "wb").write(hdr + payload)


def paint_grle(path, arr, image_channels=1):
    """Encode a 2D uint8 array as GRLE (row-major run-length). Runs found vectorized via np.diff (fast even at
    4096^2). Used e.g. for the farmland layer: field parcel = 1, the rest of the map = 2."""
    h, w = arr.shape
    flat = arr.ravel().astype(np.uint8)
    change = np.nonzero(np.diff(flat))[0] + 1
    starts = np.concatenate(([0], change)); ends = np.concatenate((change, [flat.size]))
    payload = bytearray()
    for s, e in zip(starts.tolist(), ends.tolist()):
        v = int(flat[s]); cnt = e - s
        if cnt == 1:
            payload.append(v)
        else:
            k, term = divmod(cnt - 2, 255)
            payload += bytes((v, v)) + b"\xff" * k + bytes((term,))
    open(path, "wb").write(b"GRLE" + bytes((1,)) + struct.pack("<IIII", w, h, image_channels, len(payload)) + bytes(payload))
