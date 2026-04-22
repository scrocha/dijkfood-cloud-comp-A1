import boto3
import time
import datetime

# Clientes AWS
ecs_client = boto3.client('ecs', region_name='us-east-1')
cw_client = boto3.client('cloudwatch', region_name='us-east-1')
elbv2_client = boto3.client('elbv2', region_name='us-east-1')

CLUSTER_NAME = "dijkfood-cluster"
SERVICE_NAME = "dijkfood-cadastro-service"
TG_NAME = "dijkfood-tg-cadastro"

def monitorar_ecs():
    print(f"Iniciando monitoramento do serviço: {SERVICE_NAME}...\n")
    
    # Recupera o ARN do Target Group de Cadastro para mapear a dimensão no CloudWatch
    try:
        tgs = elbv2_client.describe_target_groups(Names=[TG_NAME])
        tg_arn = tgs['TargetGroups'][0]['TargetGroupArn']
        tg_dimension = tg_arn.split(':', 5)[-1]  # Ex: targetgroup/dijkfood-tg-cadastro/6d0ec...
    except Exception as e:
        print(f"Aviso: Não consegui achar o Target Group {TG_NAME}. O deploy já rodou? Erro: {e}")
        return

    print(f"{'HORA':<10} | {'DESEJADAS':<10} | {'RODANDO':<10} | {'CPU (%)':<10} | {'REQS/TARGET (1min)':<18}")
    print("-" * 65)

    while True:
        try:
            # 1. Busca a quantidade de instâncias (Tasks)
            response_ecs = ecs_client.describe_services(
                cluster=CLUSTER_NAME,
                services=[SERVICE_NAME]
            )
            
            if not response_ecs['services']:
                print("Serviço não encontrado. O deploy já terminou?")
                time.sleep(10)
                continue
                
            svc = response_ecs['services'][0]
            desired_count = svc['desiredCount']
            running_count = svc['runningCount']

            now = datetime.datetime.utcnow()
            start_time = now - datetime.timedelta(minutes=5)

            # 2. Busca a utilização de CPU no CloudWatch
            response_cw_cpu = cw_client.get_metric_statistics(
                Namespace='AWS/ECS',
                MetricName='CPUUtilization',
                Dimensions=[
                    {'Name': 'ClusterName', 'Value': CLUSTER_NAME},
                    {'Name': 'ServiceName', 'Value': SERVICE_NAME}
                ],
                StartTime=start_time,
                EndTime=now,
                Period=60,
                Statistics=['Average']
            )

            cpu_usage = 0.0
            if response_cw_cpu['Datapoints']:
                datapoint_cpu = sorted(response_cw_cpu['Datapoints'], key=lambda x: x['Timestamp'])[-1]
                cpu_usage = datapoint_cpu['Average']

            # 3. Busca RequestCountPerTarget no CloudWatch (Métrica do TargetTracking)
            response_cw_req = cw_client.get_metric_statistics(
                Namespace='AWS/ApplicationELB',
                MetricName='RequestCountPerTarget',
                Dimensions=[
                    {'Name': 'TargetGroup', 'Value': tg_dimension}
                ],
                StartTime=start_time,
                EndTime=now,
                Period=60,
                Statistics=['Sum']
            )

            req_target = 0.0
            if response_cw_req['Datapoints']:
                datapoint_req = sorted(response_cw_req['Datapoints'], key=lambda x: x['Timestamp'])[-1]
                req_target = datapoint_req['Sum']

            hora_atual = time.strftime('%H:%M:%S')
            
            # Formata e imprime a linha
            print(f"{hora_atual:<10} | {desired_count:<10} | {running_count:<10} | {cpu_usage:<10.2f} | {req_target:.2f}")

        except Exception as e:
            print(f"Erro ao buscar métricas: {e}")

        # Aguarda 10 segundos antes de verificar novamente
        time.sleep(10)

if __name__ == "__main__":
    monitorar_ecs()