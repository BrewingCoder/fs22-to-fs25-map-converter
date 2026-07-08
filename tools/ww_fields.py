"""
ww_fields.py - shared reader: READ the FS22 original's 82 fields and UNDERSTAND them as world polygons.
Reused by build_fields (generate FS25 field nodes) and build_densities (rasterize cultivation). No copying.

FS22 field = fields/fieldNN/fieldDimensions/<corner group(s)>. Each corner group is a rectangle: cornerNN_1
(world pos) + two child corners (LOCAL offsets, rotation-aware). SIMPLE field = 1 group; COMPOUND = several
(union them). Verified logic lifted from the prior convert_fields.py (73/82->80/81 field->parcel match).
"""
import math
import re
import xml.etree.ElementTree as ET
from shapely.geometry import Polygon
from shapely.ops import unary_union


def _vec(s):
    return [float(x) for x in (s or "0 0 0").split()]


def _rotate(v, rot_deg):
    """Rotate an offset by Euler (X,Y,Z) degrees, order Rz@Ry@Rx (verified: (180,0,180) -> flip X,Z)."""
    rx, ry, rz = (math.radians(a) for a in rot_deg)
    x, y, z = v
    cx, sx = math.cos(rx), math.sin(rx); y, z = cx * y - sx * z, sx * y + cx * z
    cy, sy = math.cos(ry), math.sin(ry); x, z = cy * x + sy * z, -sy * x + cy * z
    cz, sz = math.cos(rz), math.sin(rz); x, y = cz * x - sz * y, sz * x + cz * y
    return [x, y, z]


def read_fs22_fields(fs22_i3d):
    """Return [{num:int, origin:[x,y,z], polygon:[(x,z),...] world, indicator:[x,y,z]}] for the WW fields.

    `num` is the FS22 field number parsed from the node name (`field45` -> 45). It MUST be carried through and
    used verbatim as the FS25 field name/number: distributed AutoDrive/Courseplay configs are keyed to these
    numbers, so re-enumerating (1..N) would silently break alignment whenever the source has gaps or a field is
    dropped for bad geometry. A dropped field then leaves a clean gap (base-game mapUS itself skips field25).
    """
    root = ET.parse(fs22_i3d).getroot()
    fields_grp = next(e for e in root.find("Scene").iter()
                      if e.tag == "TransformGroup" and e.get("name") == "fields")
    out = []
    for fld in list(fields_grp):
        dims = next((c for c in fld if c.get("name") == "fieldDimensions"), None)
        indicator = next((c for c in fld if c.get("name") == "fieldMapIndicator"), None)
        if dims is None or len(dims) == 0:
            continue
        m = re.search(r"\d+", fld.get("name") or "")
        if m is None:
            continue
        num = int(m.group())
        rects, origin = [], None
        for grp in list(dims):
            k = list(grp)
            if len(k) < 2:
                continue
            P1 = _vec(grp.get("translation"))
            if origin is None:
                origin = P1
            rot = _vec(grp.get("rotation")) if grp.get("rotation") else [0, 0, 0]
            v2 = _rotate(_vec(k[0].get("translation")), rot)
            v3 = _rotate(_vec(k[1].get("translation")), rot)
            quad = [(P1[0], P1[2]),
                    (P1[0] + v2[0], P1[2] + v2[2]),
                    (P1[0] + v3[0], P1[2] + v3[2]),
                    (P1[0] + v3[0] - v2[0], P1[2] + v3[2] - v2[2])]
            rects.append(Polygon(quad).buffer(0))
        if not rects or origin is None:
            continue
        u = unary_union(rects)
        geom = max(u.geoms, key=lambda g: g.area) if u.geom_type == "MultiPolygon" else u
        world = list(geom.exterior.coords)[:-1]
        name_world = _vec(indicator.get("translation")) if indicator is not None else list(origin)
        out.append(dict(num=num, origin=origin, polygon=world, indicator=name_world))
    return out
