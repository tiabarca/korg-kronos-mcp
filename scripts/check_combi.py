"""Lee la Combi del buffer de edición: timbres que suenan, efectos y diferencias con la almacenada."""


def run(ctx, stored=None, **_):
    C, P, O = ctx.catalog, ctx.P, ctx.ObjType
    cur = ctx.client.read_current_object(O.COMBINATION)
    out = {"name": C._name(cur), "size": len(cur),
           "timbres": [t for t in C.combi_timbres(cur, ctx.client.channel + 1) if t["status"] != "Off"],
           "summary": P.summary(O.COMBINATION, cur)}
    if stored:
        b, i = ctx.perf_ref(stored)
        st, _ = ctx.client.read_object(O.COMBINATION, b, i)
        out["changed_vs_stored"] = P.annotate_changes(O.COMBINATION, st, cur, max_items=80)
    return out
