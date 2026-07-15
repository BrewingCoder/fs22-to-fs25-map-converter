"""
build_tweaks.py - apply per-map manual OVERRIDES ("tweaks") from the convert config, LAST, as the final word.

Each tweak calls out ONE generated thing by name and nudges it (rotate / translate / scale / delete). This is the
escape hatch for one-off corrections the automated pipeline can't infer - e.g. a single sell point that happens to
face the wrong way in FS25 - WITHOUT hand-editing the output (convert.py clobbers the output every run, so hand
edits never survive). Put the correction here and it re-applies on every build.

Config schema  ("tweaks": [ {...}, ... ]  in <map>.convert.json):
  {
    "where":   "placeable" | "i3d" | "i3d_shape",   # WHAT artifact. default "placeable" = maps/placeables.xml.
                                         #   "i3d"       = the map scene graph (maps/<i3d>) - buildings, curtains, any NAMED node.
                                         #   "i3d_shape" = a SPATIAL selector over mesh Shape nodes in the scene graph (roads,
                                         #                 bridges, etc.) that have NO usable name - selected by WORLD-space area.
    "match":   "<name>",                # (placeable / i3d) WHICH one.
                                         #   placeable: matches storeData <name>  OR filename basename  OR uniqueId.
                                         #   i3d:       a node name, or a "parent>child>leaf" name-path.
    "action":  "rotate" | "translate" | "scale" | "delete",
    "yaw":  <deg>, "pitch": <deg>, "roll": <deg>,   # rotate: DELTAS added to the existing euler (yaw=Y, pitch=X, roll=Z)
    "dx": <m>, "dy": <m>, "dz": <m>,                # translate: DELTAS in WORLD metres (i3d_shape converts to the node's local frame)
    "scale": <factor> | "sx sy sz",                 # scale (i3d nodes only): multiply, uniform or per-axis
    "_comment": "why this tweak exists"             # free-text, ignored by the tool
  }

  i3d_shape (SPATIAL) extra keys - select mesh Shapes by WORLD position and/or shapeId/name:
    "group":    "WW_roads",             # optional: restrict the search to Shapes under this top scene-group (default: whole Scene)
    "area":     {"x": [xmin, xmax], "z": [zmin, zmax]},   # WORLD-coord XZ box; a Shape is hit if its world origin is inside.
                                         #   OPTIONAL if shape_id/name is given - omit to hit EVERY instance (e.g. fix a template quirk map-wide)
    "name":     "Street100m",           # optional: also require this substring in the node name
    "shape_id": 506,                     # optional: also require this shapeId (one mesh reused N times -> hits all N unless boxed)
    "expect":   1,                       # optional: raise unless EXACTLY this many Shapes match (safety against over/under-selecting)
    "local":    true                     # translate only: add dx/dy/dz straight to the node's LOCAL translation (like GE's Transform
                                         #   panel), instead of a world-space delta. Use when a parent chain is rotated.
    # NB: WORLD coords = in-game HUD readout MINUS mapSize/2 (WW 16x: readout - 4096). Shape ORIGINS can sit
    #     well off the visible mesh (a 100 m street's pivot may be 15 m away), so the tool PRINTS every hit's
    #     world x/y/z + shapeId - run it, read the report, refine the box. Non-local dx/dy/dz are world-space.

Matching is case-insensitive: exact on any identifier first, else unique substring. A placeable/i3d tweak that
matches NOTHING or MORE THAN ONE thing raises. An i3d_shape tweak that matches nothing raises; multiple hits are
allowed (a road arm is several Shapes) unless "expect" is set. Runs after every generator (incl. fixup) so it
always has the final say.
"""
import os, sys, json, math
import numpy as np
import xml.etree.ElementTree as ET

WW = os.environ.get("FS_CONVERT_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONV = json.load(open(os.path.join(WW, "tools", os.environ.get("MAP_CONVERT", "wildwest.convert.json")), encoding="utf-8"))
OUT = os.path.join(WW, "out", CONV["identity"]["mod"])
MAPS = os.path.join(OUT, "maps")
I3D = CONV["identity"]["i3d"]


def _norm(a):
    """Normalize an angle to [-180, 180) - out-of-range euler components silently hang the FS25 load."""
    return (a + 180.0) % 360.0 - 180.0


def _g(tw, *keys, default=0.0):
    for k in keys:
        if k in tw:
            return float(tw[k])
    return default


# ---- placeable target (maps/placeables.xml) --------------------------------------------------------------------

def _placeable_name(pl):
    """Resolve a <placeable>'s human-readable label from its referenced (generated) station/production XML."""
    fn = pl.get("filename", "")
    rel = fn.replace("$mapdir$/", "").replace("\\", "/")
    path = os.path.join(OUT, *rel.split("/")) if rel.startswith("maps/") else None
    if path and os.path.exists(path):
        try:
            nm = ET.parse(path).getroot().find(".//storeData/name")
            if nm is not None and (nm.text or "").strip():
                return nm.text.strip()
        except ET.ParseError:
            pass
    return None


def _placeable_ids(pl):
    """All the strings a 'match' may target for one placeable: display name, filename basename, uniqueId."""
    ids = []
    nm = _placeable_name(pl)
    if nm:
        ids.append(nm)
    fn = pl.get("filename", "")
    if fn:
        ids.append(os.path.splitext(os.path.basename(fn))[0])
    if pl.get("uniqueId"):
        ids.append(pl.get("uniqueId"))
    return ids


def _apply_placeable(pl, tw):
    action = tw["action"]
    if action == "delete":
        return "delete"   # signal caller to remove
    if action == "rotate":
        rx, ry, rz = ((pl.get("rotation") or "0 0 0").split() + ["0", "0", "0"])[:3]
        rx, ry, rz = float(rx), float(ry), float(rz)
        rx = _norm(rx + _g(tw, "pitch")); ry = _norm(ry + _g(tw, "yaw")); rz = _norm(rz + _g(tw, "roll"))
        pl.set("rotation", f"{rx:g} {ry:g} {rz:g}")
        return f"rotation -> {pl.get('rotation')}"
    if action == "translate":
        x, y, z = ((pl.get("position") or "0 0 0").split() + ["0", "0", "0"])[:3]
        x, y, z = float(x) + _g(tw, "dx"), float(y) + _g(tw, "dy"), float(z) + _g(tw, "dz")
        pl.set("position", f"{x:.3f} {y:.3f} {z:.3f}")
        return f"position -> {pl.get('position')}"
    raise SystemExit(f"[tweaks] action '{action}' not supported for a placeable (use rotate/translate/delete)")


def _do_placeables(tweaks):
    pxml = os.path.join(MAPS, "placeables.xml")
    tree = ET.parse(pxml); root = tree.getroot()
    kids = list(root)
    changed = 0
    for tw in tweaks:
        target = (tw.get("match") or "").strip().lower()
        hits = [pl for pl in kids if any(target == i.lower() for i in _placeable_ids(pl))]
        if not hits:   # fall back to unique substring
            hits = [pl for pl in kids if any(target in i.lower() for i in _placeable_ids(pl))]
        if not hits:
            raise SystemExit(f"[tweaks] no placeable matches '{tw.get('match')}' - check the name")
        if len(hits) > 1:
            names = ", ".join(_placeable_ids(h)[0] for h in hits)
            raise SystemExit(f"[tweaks] '{tw.get('match')}' is ambiguous, matched {len(hits)}: {names} - be more specific")
        pl = hits[0]
        label = _placeable_ids(pl)[0]
        res = _apply_placeable(pl, tw)
        if res == "delete":
            root.remove(pl)
            print(f"[tweaks] placeable '{label}': DELETED")
        else:
            print(f"[tweaks] placeable '{label}': {res}")
        changed += 1
    if changed:
        tree.write(pxml, encoding="utf-8", xml_declaration=True)
    return changed


# ---- i3d node target (maps/<i3d> scene graph) ------------------------------------------------------------------

def _find_nodes(scene, match):
    """Match a node by exact name, a '>'-separated name-path, or (fallback) unique substring of the name."""
    parts = [p.strip() for p in match.split(">") if p.strip()]
    if len(parts) > 1:   # explicit name-path parent>child>leaf
        frontier = [scene]
        for p in parts:
            frontier = [c for n in frontier for c in n if c.get("name") == p]
        return frontier
    all_named = [n for n in scene.iter() if n.get("name")]
    exact = [n for n in all_named if n.get("name") == match]
    if exact:
        return exact
    return [n for n in all_named if match.lower() in n.get("name").lower()]


def _apply_i3d(node, tw):
    action = tw["action"]
    if action == "rotate":
        rx, ry, rz = ((node.get("rotation") or "0 0 0").split() + ["0", "0", "0"])[:3]
        rx = _norm(float(rx) + _g(tw, "pitch")); ry = _norm(float(ry) + _g(tw, "yaw")); rz = _norm(float(rz) + _g(tw, "roll"))
        node.set("rotation", f"{rx:g} {ry:g} {rz:g}")
        return f"rotation -> {node.get('rotation')}"
    if action == "translate":
        x, y, z = ((node.get("translation") or "0 0 0").split() + ["0", "0", "0"])[:3]
        x, y, z = float(x) + _g(tw, "dx"), float(y) + _g(tw, "dy"), float(z) + _g(tw, "dz")
        node.set("translation", f"{x:g} {y:g} {z:g}")
        return f"translation -> {node.get('translation')}"
    if action == "scale":
        sv = tw.get("scale", 1.0)
        sx = sy = sz = float(sv) if not isinstance(sv, str) else None
        if sx is None:
            sx, sy, sz = (float(v) for v in sv.split())
        cx, cy, cz = ((node.get("scale") or "1 1 1").split() + ["1", "1", "1"])[:3]
        node.set("scale", f"{float(cx)*sx:g} {float(cy)*sy:g} {float(cz)*sz:g}")
        return f"scale -> {node.get('scale')}"
    if action == "delete":
        return "delete"
    raise SystemExit(f"[tweaks] action '{action}' not supported for an i3d node")


def _do_i3d(tweaks):
    ip = os.path.join(MAPS, I3D)
    tree = ET.parse(ip); root = tree.getroot()
    scene = root.find("Scene")
    # map child -> parent so we can delete
    parent = {c: p for p in scene.iter() for c in p}
    changed = 0
    for tw in tweaks:
        hits = _find_nodes(scene, (tw.get("match") or "").strip())
        if not hits:
            raise SystemExit(f"[tweaks] no i3d node matches '{tw.get('match')}'")
        if len(hits) > 1:
            raise SystemExit(f"[tweaks] i3d match '{tw.get('match')}' is ambiguous ({len(hits)} nodes) - use a parent>child>leaf path")
        node = hits[0]
        res = _apply_i3d(node, tw)
        if res == "delete":
            parent[node].remove(node)
            print(f"[tweaks] i3d node '{tw.get('match')}': DELETED")
        else:
            print(f"[tweaks] i3d node '{node.get('name')}': {res}")
        changed += 1
    if changed:
        tree.write(ip, encoding="utf-8", xml_declaration=True)
    return changed


# ---- i3d_shape target: SPATIAL selector over mesh Shape nodes (roads/bridges - no usable names) ---------------

def _mat(node):
    """Local TRS matrix of a scene node (XYZ-euler, degrees), matching the converter's other tools."""
    t = [float(x) for x in (node.get("translation") or "0 0 0").split()]
    r = [math.radians(float(x)) for x in (node.get("rotation") or "0 0 0").split()]
    s = [float(x) for x in (node.get("scale") or "1 1 1").split()]
    cx, cy, cz = (math.cos(a) for a in r); sx, sy, sz = (math.sin(a) for a in r)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    M = np.eye(4); M[:3, :3] = (Rx @ Ry @ Rz) * np.array(s); M[:3, 3] = t
    return M


def _shape_rows(scene, group):
    """[(shapeNode, parentWorldMatrix, worldOrigin)] for every Shape under `group` (or the whole Scene)."""
    if group:
        roots = [n for n in scene.iter() if n.get("name") == group]
        if not roots:
            raise SystemExit(f"[tweaks] i3d_shape group '{group}' not found in the scene")
    else:
        roots = [scene]
    rows = []

    def walk(n, M0):
        M = M0 @ _mat(n)
        if n.tag == "Shape":
            rows.append((n, M0, M[:3, 3]))
        for ch in n:
            if ch.tag in ("TransformGroup", "Shape"):
                walk(ch, M)
    for r in roots:
        walk(r, np.eye(4))
    return rows


def _apply_shape(node, parentM, tw):
    action = tw["action"]
    if action == "delete":
        return "delete"
    if action == "translate":
        if tw.get("local"):                                    # LOCAL frame: dx/dy/dz added straight to the node's own
            d_l = np.array([_g(tw, "dx"), _g(tw, "dy"), _g(tw, "dz")])   # translation, exactly like typing in GE's Transform panel
        else:
            d_w = np.array([_g(tw, "dx"), _g(tw, "dy"), _g(tw, "dz")])
            d_l = np.linalg.inv(parentM[:3, :3]) @ d_w          # WORLD delta -> this node's local frame (parent rot/scale)
        x, y, z = ((node.get("translation") or "0 0 0").split() + ["0", "0", "0"])[:3]
        x, y, z = float(x) + d_l[0], float(y) + d_l[1], float(z) + d_l[2]
        node.set("translation", f"{x:g} {y:g} {z:g}")
        return f"translation -> {node.get('translation')}"
    if action == "rotate":
        rx, ry, rz = ((node.get("rotation") or "0 0 0").split() + ["0", "0", "0"])[:3]
        rx = _norm(float(rx) + _g(tw, "pitch")); ry = _norm(float(ry) + _g(tw, "yaw")); rz = _norm(float(rz) + _g(tw, "roll"))
        node.set("rotation", f"{rx:g} {ry:g} {rz:g}")
        return f"rotation -> {node.get('rotation')}"
    raise SystemExit(f"[tweaks] i3d_shape action '{action}' not supported (use translate/rotate/delete)")


def _do_i3d_shapes(tweaks):
    ip = os.path.join(MAPS, I3D)
    tree = ET.parse(ip); root = tree.getroot()
    scene = root.find("Scene")
    parent = {c: p for p in scene.iter() for c in p}
    changed = 0
    for tw in tweaks:
        area = tw.get("area")
        if area is not None and ("x" not in area or "z" not in area):
            raise SystemExit(f"[tweaks] i3d_shape 'area' needs both x:[min,max] and z:[min,max] (world coords)")
        if area is None and "shape_id" not in tw and "name" not in tw:
            raise SystemExit(f"[tweaks] i3d_shape needs an 'area' box OR a 'shape_id'/'name' filter (else it would match everything)")
        rows = _shape_rows(scene, tw.get("group"))
        if area is not None:
            (xlo, xhi), (zlo, zhi) = sorted(area["x"]), sorted(area["z"])
            hits = [(n, M0, w) for (n, M0, w) in rows if xlo <= w[0] <= xhi and zlo <= w[2] <= zhi]
        else:
            hits = list(rows)                                  # no box: filter purely by shape_id/name below
        if "name" in tw:
            hits = [(n, M0, w) for (n, M0, w) in hits if str(tw["name"]).lower() in (n.get("name") or "").lower()]
        if "shape_id" in tw:
            hits = [(n, M0, w) for (n, M0, w) in hits if n.get("shapeId") == str(tw["shape_id"])]
        if not hits:
            raise SystemExit(f"[tweaks] i3d_shape matched NO shape"
                             + (f" in area x{area['x']} z{area['z']}" if area is not None else " (whole group)")
                             + (f" name~'{tw['name']}'" if 'name' in tw else "")
                             + (f" shapeId={tw['shape_id']}" if 'shape_id' in tw else ""))
        if "expect" in tw and len(hits) != int(tw["expect"]):
            listing = "; ".join(f"id{n.get('shapeId')}@({w[0]:.1f},{w[1]:.2f},{w[2]:.1f})" for n, _, w in hits)
            raise SystemExit(f"[tweaks] i3d_shape expected {tw['expect']} shape(s), matched {len(hits)}: {listing}")
        for n, M0, w in hits:
            res = _apply_shape(n, M0, tw)
            if res == "delete":
                parent[n].remove(n)
                print(f"[tweaks] i3d_shape id{n.get('shapeId')} @world({w[0]:.1f},{w[1]:.2f},{w[2]:.1f}): DELETED")
            else:
                print(f"[tweaks] i3d_shape id{n.get('shapeId')} @world({w[0]:.1f},{w[1]:.2f},{w[2]:.1f}): {res}")
            changed += 1
    if changed:
        tree.write(ip, encoding="utf-8", xml_declaration=True)
    return changed


def main():
    tw_cfg = CONV.get("tweaks", [])
    tweaks = tw_cfg.get("list", []) if isinstance(tw_cfg, dict) else tw_cfg   # config uses {"_note":..,"list":[..]}
    tweaks = [t for t in tweaks if isinstance(t, dict) and t.get("action")]   # ignore _comment-only / notes
    if not tweaks:
        print("[tweaks] none configured")
        return
    by_where = {"placeable": [], "i3d": [], "i3d_shape": []}
    for t in tweaks:
        where = t.get("where", "placeable")
        if where not in by_where:
            raise SystemExit(f"[tweaks] unknown 'where': {where!r} (use 'placeable', 'i3d', or 'i3d_shape')")
        by_where[where].append(t)
    n = 0
    n += _do_placeables(by_where["placeable"]) if by_where["placeable"] else 0
    n += _do_i3d(by_where["i3d"]) if by_where["i3d"] else 0
    n += _do_i3d_shapes(by_where["i3d_shape"]) if by_where["i3d_shape"] else 0
    print(f"[tweaks] applied {n} tweak(s)")


if __name__ == "__main__":
    main()
