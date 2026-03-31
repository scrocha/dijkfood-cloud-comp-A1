import boto3
import time
import psycopg2
import seed_db 

AWS_REGION = "us-east-1"
DB_IDENTIFIER = "dijkfood-db-instance"
DB_NAME = "dijkfood"
DB_USER = "postgres"
DB_PASSWORD = "SuperSecretPassword123!" 
DB_PORT = 5432

rds_client = boto3.client('rds', region_name=AWS_REGION)
ec2_client = boto3.client('ec2', region_name=AWS_REGION)

def setup_security_group():
    """Cria um Security Group permitindo acesso na porta 5432"""

    print("Configurando regras de rede (Security Group)...")
    
    # pega a VPC padrão da conta AWS
    vpcs = ec2_client.describe_vpcs(Filters=[{'Name': 'isDefault', 'Values': ['true']}])
    vpc_id = vpcs['Vpcs'][0]['VpcId']
    
    try:
        sg_response = ec2_client.create_security_group(
            GroupName='dijkfood-db-sg',
            Description='Permite acesso ao PostgreSQL para o DijkFood',
            VpcId=vpc_id
        )
        sg_id = sg_response['GroupId']
        
        # libera a porta 5432 para a internet (para o script rodar localmente)
        ec2_client.authorize_security_group_ingress(
            GroupId=sg_id,
            IpPermissions=[{
                'IpProtocol': 'tcp',
                'FromPort': DB_PORT,
                'ToPort': DB_PORT,
                'IpRanges': [{'CidrIp': '0.0.0.0/0'}]
            }]
        )

        print("Security Group criado com sucesso!")
        return sg_id

    except Exception as e:
        if "InvalidGroup.Duplicate" in str(e):
            print("Security Group já existe. Buscando o ID...")

            sgs = ec2_client.describe_security_groups(GroupNames=['dijkfood-db-sg'])
            return sgs['SecurityGroups'][0]['GroupId']
        raise e

def create_rds_instance(sg_id):
    """Provisiona o banco PostgreSQL na AWS"""

    print("Iniciando a criação do RDS...")
    
    try:
        rds_client.create_db_instance(
            DBInstanceIdentifier=DB_IDENTIFIER,
            AllocatedStorage=20, # 20GB
            DBInstanceClass='db.t3.micro', # mais barata
            Engine='postgres',
            EngineVersion='15',
            MasterUsername=DB_USER,
            MasterUserPassword=DB_PASSWORD,
            DBName=DB_NAME,
            VpcSecurityGroupIds=[sg_id],
            PubliclyAccessible=True
        )
    except Exception as e:
        if "DBInstanceAlreadyExists" not in str(e):
            raise e

    # pausa o python até o banco ficar disponível
    waiter = rds_client.get_waiter('db_instance_available')
    waiter.wait(DBInstanceIdentifier=DB_IDENTIFIER)
    
    # pega o endpoint gerado pela AWS
    response = rds_client.describe_db_instances(DBInstanceIdentifier=DB_IDENTIFIER)
    endpoint = response['DBInstances'][0]['Endpoint']['Address']

    print(f"RDS Disponível! Endpoint: {endpoint}")
    
    return endpoint

def run_database_scripts(endpoint):
    """Executa o DDL e o seu script de popular a base"""

    print("Criando tabelas (Executando DDL.sql)...")
    
    # algum tempo para o DNS propagar
    time.sleep(10) 
    
    try:
        conn = psycopg2.connect(
            host=endpoint, database=DB_NAME, user=DB_USER, password=DB_PASSWORD
        )
        conn.autocommit = True
        cursor = conn.cursor()
        
        # lê o ddl.sql e executa lá na AWS
        with open('ddl.sql', 'r', encoding='utf-8') as file:
            ddl_script = file.read()

        cursor.execute(ddl_script)
        
        cursor.close()
        conn.close()
        print("Tabelas criadas com sucesso!")
        
        print("Populando o banco de dados via seed_db.py...")

        # variaveis de conexão
        seed_db.DB_HOST = endpoint
        seed_db.DB_PASS = DB_PASSWORD
        
        # chama o seed_db.py
        seed_db.main()
        print("Execução do seed_db.py concluída! Base de dados populada!")
        
    except Exception as e:
        print(f"Erro ao interagir com o banco: {e}")

def destroy_infrastructure(sg_id):
    """Deleta o RDS e o Security Group para evitar cobranças"""
    
    print("Iniciando a destruição dos recursos AWS...")
    
    try:
        rds_client.delete_db_instance(
            DBInstanceIdentifier=DB_IDENTIFIER,
            SkipFinalSnapshot=True # pula o snapshot final para não gerar custos
        )
        
        print("Aguardando a exclusão completa do banco...")

        waiter = rds_client.get_waiter('db_instance_deleted') # espera o banco ser deletado
        waiter.wait(DBInstanceIdentifier=DB_IDENTIFIER)
        print("Banco de dados destruído.")
        
        # deleto também o security group
        ec2_client.delete_security_group(GroupId=sg_id)
        print("Security Group destruído.")
        
    except Exception as e:
        print(f"Erro durante a destruição: {e}")

def main():
    sg_id = None

    try:
        # configura a rede
        sg_id = setup_security_group()
        
        # cria o banco
        endpoint = create_rds_instance(sg_id)
        
        # cria as tabelas e insere os dados
        run_database_scripts(endpoint)
        
    finally:
        # destrói tudo 
        if sg_id:
            destroy_infrastructure(sg_id)

        print("Execução do deploy.py finalizada.")

if __name__ == "__main__":
    main()