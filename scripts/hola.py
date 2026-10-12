"""Ejemplo de script en caliente: run_script('hola')."""


def run(ctx, **args):
    return {"identity": ctx.client.identify(), "args": args}
