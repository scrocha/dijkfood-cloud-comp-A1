"""python -m carga_unitario <combo.json>"""

import sys

from .runner import run_from_json, run_internal_single
from .scenarios import SCENARIOS


def main():
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help"):
        print("Uso: python -m carga_unitario <combo.json>")
        print("     python -m carga_unitario --list")
        sys.exit(0)

    if args[0] == "--list":
        print("Cenários disponíveis:")
        for name in sorted(SCENARIOS):
            print(f"  {name}")
        sys.exit(0)

    if args[0] == "--_internal_single":
        run_internal_single(args[1], args[2])
        sys.exit(0)

    run_from_json(args[0])


main()
