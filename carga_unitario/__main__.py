"""python -m carga_unitario <combo.json>"""

import sys

from .runner import run_from_json, run_internal_single
from .scenarios import SCENARIOS


def main():
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help"):
        print("Uso: python -m carga_unitario <combo.json> [--destroy-after]")
        print("     URLs: .env na raiz + carga_unitario/.env (pacote sobrescreve raiz); VITE_* como demo-ui.")
        print("           CADASTRO/ROTAS/PEDIDOS e VITE_* do arquivo substituem export no shell.")
        print("     python -m carga_unitario --list")
        print("     python -m carga_unitario --flow <fluxo_config.json>")
        print("     --destroy-after  ao fim roda clear_data_only.py (DDL Postgres + esvaziar Dynamo; mantém ECS/ALB)")
        print("                      Para remover infra: uv run python destroy.py")
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

    if args[0] == "--flow":
        if len(args) < 2:
            print("Erro: --flow requer caminho para o JSON de configuração.")
            sys.exit(1)
        from .fluxo import run_flow_from_json
        run_flow_from_json(args[1])
        sys.exit(0)

    if args[0] == "--_internal_single":
        run_internal_single(args[1], args[2])
        sys.exit(0)

    run_from_json(args[0], destroy_after=destroy_after)


main()
