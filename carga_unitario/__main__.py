"""python -m carga_unitario <combo.json>"""

import sys

from .runner import run_from_json, run_internal_single
from .scenarios import SCENARIOS


def main():
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help"):
        print("Uso: python -m carga_unitario <combo.json> [--destroy-after]")
        print("     python -m carga_unitario --list")
        print("     --destroy-after  ao fim roda destroy.py --soft (ECS/ALB/Dynamo + DDL Postgres)")
        sys.exit(0)

    destroy_after = False
    rest: list[str] = []
    for a in args:
        if a == "--destroy-after":
            destroy_after = True
        else:
            rest.append(a)
    args = rest

    if not args:
        print("Erro: informe o JSON de cenários.")
        sys.exit(1)

    if args[0] == "--list":
        print("Cenários disponíveis:")
        for name in sorted(SCENARIOS):
            print(f"  {name}")
        sys.exit(0)

    if args[0] == "--_internal_single":
        run_internal_single(args[1], args[2])
        sys.exit(0)

    run_from_json(args[0], destroy_after=destroy_after)


main()
