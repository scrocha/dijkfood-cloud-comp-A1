from pydantic import BaseModel, Field, ConfigDict
from typing import List, Dict

LATITUDE_RANGE = (-23.9857223, -23.3590754)
LONGITUDE_RANGE = (-46.8253578, -46.3653906)

class Ponto(BaseModel):
    model_config = ConfigDict(extra="ignore") 
    
    lat: float = Field(..., ge=LATITUDE_RANGE[0], le=LATITUDE_RANGE[1], description="Latitude")
    lon: float = Field(..., ge=LONGITUDE_RANGE[0], le=LONGITUDE_RANGE[1], description="Longitude")

class EntregadorRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    
    restaurante: Ponto
    entregadores: List[Ponto] = Field(..., min_length=1) 

class RotaRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    origem: Ponto
    destino: Ponto

class RotaResponseData(BaseModel):
    distancia_metros: float
    nos: int
    coordenadas: List[Dict[str, float]]
    origem_projetada: Ponto
    destino_projetado: Ponto

class EntregadorResponse(BaseModel):
    entregador_idx: int
    entregador_original: Ponto
    rota_ao_restaurante: RotaResponseData

class RotaEntregaResponse(BaseModel):
    restaurante_solicitado: Ponto
    cliente_solicitado: Ponto
    dados_rota: RotaResponseData