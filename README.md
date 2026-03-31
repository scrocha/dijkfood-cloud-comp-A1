# dijkfood-cloud-comp-A1

## Database 

#### Local

```shell
# cria o banco de dados
docker run --name dijkfood-db -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=dijkfood -p 5432:5432 -d postgres:15

# cria o schema e as tabelas
Get-Content DDL.sql | docker exec -i dijkfood-db psql -U postgres -d dijkfood
```

```python
# faz a carga inicial de dados
uv run python seed_db.py
```

```shell
# conecta ao banco de dados
docker exec -it dijkfood-db psql -U postgres -d dijkfood
```