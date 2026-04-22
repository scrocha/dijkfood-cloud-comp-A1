import boto3
import time
import sys

AWS_REGION = "us-east-1"
CLUSTER_NAME = "dijkfood-cluster"
TASK_FAMILY = "dijkfood-pedidos-task"

ecs = boto3.client("ecs", region_name=AWS_REGION)

def get_running_tasks():
    # Filtra apenas pela família da task de pedidos
    response = ecs.list_tasks(
        cluster=CLUSTER_NAME, 
        family=TASK_FAMILY, 
        desiredStatus='RUNNING'
    )
    return response.get('taskArns', [])

def kill_random_task(task_arns):
    if not task_arns:
        print("Nenhuma tarefa rodando encontrada.")
        return None
    
    target = task_arns[0]
    print(f"--- MATANDO TAREFA: {target.split('/')[-1]} ---")
    ecs.stop_task(cluster=CLUSTER_NAME, task=target, reason="Teste de tolerância a falhas (Chaos Test)")
    return target

def monitor_recovery(old_task_arn):
    print("Aguardando o ECS detectar a falha e subir uma nova instância...")
    start_time = time.time()
    
    while True:
        tasks = get_running_tasks()
        # Se a antiga sumiu e temos uma nova (ou a mesma quantidade de antes)
        if old_task_arn not in tasks and len(tasks) >= 1:
            print(f"\n[SUCESSO] O sistema se recuperou!")
            print(f"Nova(s) tarefa(s) detectada(s): {[t.split('/')[-1] for t in tasks]}")
            break
            
        sys.stdout.write(".")
        sys.stdout.flush()
        time.sleep(2)
        
        if time.time() - start_time > 300: # 5 minutos timeout
            print("\n[ERRO] O sistema demorou demais para se recuperar.")
            break

if __name__ == "__main__":
    print("--- INICIANDO DEMONSTRAÇÃO DE TOLERÂNCIA A FALHAS ---")
    
    tasks = get_running_tasks()
    if not tasks:
        print("Erro: Nenhuma tarefa encontrada no cluster. Certifique-se de que o serviço está rodando.")
        sys.exit(1)
    
    print(f"Tarefas atuais: {len(tasks)}")
    
    old_task = kill_random_task(tasks)
    if old_task:
        monitor_recovery(old_task)
