from datetime import date
from pydantic import BaseModel, Field, EmailStr, ConfigDict

class Usuario(BaseModel):
    user_id: str = Field(..., max_length=255)
    primeiro_nome: str = Field(..., max_length=255)
    ultimo_nome: str = Field(..., max_length=255)
    email: EmailStr = Field(...)
    telefone: str = Field(..., max_length=20)
    senha: str = Field(..., max_length=255)
    data_nascimento: date
    endereco_latitude: float = Field(..., ge=-90.0, le=90.0)
    endereco_longitude: float = Field(..., ge=-180.0, le=180.0)

    model_config = ConfigDict(from_attributes=True)


class Restaurante(BaseModel):
    rest_id: str = Field(..., max_length=255)
    nome: str = Field(..., max_length=255)
    tipo_cozinha: str = Field(..., max_length=100)
    endereco_latitude: float = Field(..., ge=-90.0, le=90.0)
    endereco_longitude: float = Field(..., ge=-180.0, le=180.0)

    model_config = ConfigDict(from_attributes=True)


class Produto(BaseModel):
    prod_id: str = Field(..., max_length=255)
    nome: str = Field(..., max_length=255)
    rest_id: str = Field(..., max_length=255)

    model_config = ConfigDict(from_attributes=True)


class Entregador(BaseModel):
    entregador_id: str = Field(..., max_length=255)
    nome: str = Field(..., max_length=255)
    tipo_veiculo: str = Field(..., max_length=50)
    endereco_latitude: float = Field(..., ge=-90.0, le=90.0)
    endereco_longitude: float = Field(..., ge=-180.0, le=180.0)

    model_config = ConfigDict(from_attributes=True)