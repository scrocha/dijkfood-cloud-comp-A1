import requests
import sys
import os

def test_endpoint(name, url):
    print(f"Testando {name}: {url}...", end=" ")
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            print("OK!")
            return True
        else:
            print(f"ERRO (Status: {response.status_code})")
            return False
    except Exception as e:
        print(f"ERRO ({e})")
        return False

def main():
    if len(sys.argv) < 2:
        print("Uso: python test_infra.py <ALB_DNS>")
        sys.exit(1)
    
    alb_dns = sys.argv[1]
    if not alb_dns.startswith("http"):
        alb_dns = f"http://{alb_dns}"

    print(f"\nIniciando testes de infraestrutura no Load Balancer: {alb_dns}")
    print("=" * 60)

    results = []
    
    # 1. Testar Cadastro (RDS)
    results.append(test_endpoint("API Cadastro (Docs)", f"{alb_dns}/docs"))

    # 2. Testar Rotas (Route Service)
    results.append(test_endpoint("API Rotas (Health)", f"{alb_dns}/rotas/health"))

    # 3. Testar Pedidos (DynamoDB)
    results.append(test_endpoint("API Pedidos (Health)", f"{alb_dns}/pedidos/health"))

    # 4. Teste funcional simples no DynamoDB (Criar Pedido)
    print("\nTestando criação de pedido no DynamoDB...", end=" ")
    try:
        order_data = {
            "customer_id": "test_user_123",
            "restaurant_id": "test_rest_456",
            "items": [{"name": "Pizza", "quantity": 1, "price": 50.0}],
            "total_value": 50.0
        }
        # Nota: O prefixo /pedidos já está configurado no ALB e no root_path do FastAPI
        resp = requests.post(f"{alb_dns}/pedidos/orders", json=order_data, timeout=10)
        if resp.status_code == 201:
            print("OK! Pedido criado com sucesso.")
            order_id = resp.json().get("order_id")
            print(f"ID do Pedido: {order_id}")
            results.append(True)
        else:
            print(f"ERRO ao criar pedido (Status: {resp.status_code}, Detalhe: {resp.text})")
            results.append(False)
    except Exception as e:
        print(f"ERRO excepcional ao criar pedido: {e}")
        results.append(False)

    print("=" * 60)
    if all(results):
        print("TODOS OS TESTES PASSARAM COM SUCESSO!")
    else:
        print("ALGUNS TESTES FALHARAM. Verifique os logs acima.")
    print("=" * 60)

if __name__ == "__main__":
    main()
