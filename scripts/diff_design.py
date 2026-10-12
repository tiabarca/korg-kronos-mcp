"""Compara lo que se calculó en el último diseño con lo que el Kronos tiene guardado.
Uso: run_script('diff_design', {'target': 'USER-G 018', 'type': 'COMBI'})"""
import json
import re


def run(ctx, target, type="COMBI", **_):
    O = ctx.ObjType
    obj = O.COMBINATION if type.upper().startswith("COMBI") else O.PROGRAM
    label = "COMBI" if obj == O.COMBINATION else "PROGRAM"
    recs = sorted((ctx.DATA_DIR / "designs").glob(f"*_{label}_*.json"))
    rec = next(json.loads(p.read_text(encoding="utf-8")) for p in reversed(recs)
               if json.loads(p.read_text(encoding="utf-8"))["target"] == target)
    bks = sorted(ctx.BACKUP_DIR.glob(f"*_{label}_{target.replace(' ', '_')}_antes.syx"))
    raw = bks[-1].read_bytes()
    m = re.search(rb"\xF0[^\xF7]*\xF7", raw, re.S)
    from kronos_mcp.protocol.messages import parse
    from kronos_mcp.protocol.codec import decode_7bit
    base = decode_7bit(parse(m.group(0)).data[5:])
    ops = []
    cache = {}
    for ch in rec["changes"]:
        ch = dict(ch)
        if "from" in ch:
            kind = O.COMBINATION if str(ch.pop("from_type", "PROGRAM")).upper().startswith("COMBI") else O.PROGRAM
            key = (kind, ch["from"])
            if key not in cache:
                b, i = ctx.perf_ref(ch["from"])
                cache[key], _ = ctx.client.read_object(kind, b, i)
            ch["from"] = cache[key]
        ops.append(ch)
    if rec.get("name"):
        ops.append({"name": rec["name"]})
    expected, _ = ctx.design.apply(obj, base, ops)
    b, i = ctx.perf_ref(target)
    stored, _ = ctx.client.read_object(obj, b, i)
    diff_bytes = [k for k in range(len(stored)) if stored[k] != expected[k]]
    return {"backup": bks[-1].name, "diff_bytes": len(diff_bytes), "offsets": diff_bytes[:40],
            "params": ctx.P.annotate_changes(obj, expected, stored, max_items=40)}
