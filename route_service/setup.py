import osmnx as ox
import boto3
import os

PLACE_NAME = "Sao Paulo, SP, Brazil"
GRAPH_FILE_NAME = "grafo_sp.graphml"

AWS_BUCKET_NAME = "grafo-dijkfood-sp"
AWS_REGION = "us-east-1"

def get_s3_client():
    return boto3.client("s3", region_name=AWS_REGION)

def criar_bucket(s3, bucket_name):
    try:
        s3.head_bucket(Bucket=bucket_name)
        print(f"Bucket '{bucket_name}' já existe")
    except:
        try:
            print(f"Criando bucket '{bucket_name}'")
            if AWS_REGION == "us-east-1":
                s3.create_bucket(Bucket=bucket_name)
            else:
                s3.create_bucket(
                    Bucket=bucket_name,
                    CreateBucketConfiguration={"LocationConstraint": AWS_REGION},
                )
            print("Bucket criado com sucesso")
        except Exception as e:
            print(f"Erro ao criar bucket: {e}")
            raise


def upload_para_s3(s3, caminho_local, bucket_name, nome_s3):
    print(f"Enviando '{caminho_local}' para s3://{bucket_name}/{nome_s3}")
    s3.upload_file(caminho_local, bucket_name, nome_s3)
    print("Upload concluído!")


def baixar_grafo():
    G = ox.graph_from_place(
        PLACE_NAME,
        network_type="drive",
        simplify=True
    )
    print("Grafo baixado")
    return G

def carregar_grafo(nome_arquivo="grafo_sp.graphml"):
    G = ox.load_graphml(nome_arquivo)
    return G

def salvar_grafo(G, nome_arquivo="grafo_sp.graphml"):
    ox.save_graphml(G, nome_arquivo)


if __name__ == "__main__":
    if not os.path.exists(GRAPH_FILE_NAME):
        G = baixar_grafo()
        salvar_grafo(G, GRAPH_FILE_NAME)

    # 2. Upload para S3
    s3 = get_s3_client()
    criar_bucket(s3, AWS_BUCKET_NAME)
    upload_para_s3(s3, GRAPH_FILE_NAME, AWS_BUCKET_NAME, GRAPH_FILE_NAME)