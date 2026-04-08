import { urlCadastro } from "../lib/env";
import { ApiError, getJson, postJson } from "../lib/http";
import { asRecord, numField, strField } from "../lib/record";

export type UsuarioDTO = {
  user_id: string;
  primeiro_nome: string;
  ultimo_nome: string;
  email: string;
  telefone: string;
  endereco_latitude: number;
  endereco_longitude: number;
};

export type RestauranteDTO = {
  rest_id: string;
  nome: string;
  tipo_cozinha: string;
  endereco_latitude: number;
  endereco_longitude: number;
};

export type EntregadorDTO = {
  entregador_id: string;
  nome: string;
  tipo_veiculo: string;
  endereco_latitude: number;
  endereco_longitude: number;
};

export type ProdutoCreate = {
  prod_id: string;
  nome: string;
  rest_id: string;
};

function mapUsuario(row: Record<string, unknown>): UsuarioDTO {
  return {
    user_id: strField(row, "user_id", "USER_ID"),
    primeiro_nome: strField(row, "primeiro_nome", "PRIMEIRO_NOME"),
    ultimo_nome: strField(row, "ultimo_nome", "ULTIMO_NOME"),
    email: strField(row, "email", "EMAIL"),
    telefone: strField(row, "telefone", "TELEFONE"),
    endereco_latitude: numField(row, "endereco_latitude", "ENDERECO_LATITUDE"),
    endereco_longitude: numField(row, "endereco_longitude", "ENDERECO_LONGITUDE"),
  };
}

function mapRestaurante(row: Record<string, unknown>): RestauranteDTO {
  return {
    rest_id: strField(row, "rest_id", "REST_ID"),
    nome: strField(row, "nome", "NOME"),
    tipo_cozinha: strField(row, "tipo_cozinha", "TIPO_COZINHA"),
    endereco_latitude: numField(row, "endereco_latitude", "ENDERECO_LATITUDE"),
    endereco_longitude: numField(row, "endereco_longitude", "ENDERECO_LONGITUDE"),
  };
}

function mapEntregador(row: Record<string, unknown>): EntregadorDTO {
  return {
    entregador_id: strField(row, "entregador_id", "ENTREGADOR_ID"),
    nome: strField(row, "nome", "NOME"),
    tipo_veiculo: strField(row, "tipo_veiculo", "TIPO_VEICULO"),
    endereco_latitude: numField(row, "endereco_latitude", "ENDERECO_LATITUDE"),
    endereco_longitude: numField(row, "endereco_longitude", "ENDERECO_LONGITUDE"),
  };
}

export async function fetchUsuario(userId: string): Promise<UsuarioDTO | null> {
  try {
    const data = await getJson(urlCadastro(`/usuarios/${encodeURIComponent(userId)}`));
    return mapUsuario(asRecord(data));
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  }
}

/** Lança ApiError se não for 404. */
export async function getUsuarioOrThrow(userId: string): Promise<UsuarioDTO> {
  const data = await getJson(urlCadastro(`/usuarios/${encodeURIComponent(userId)}`));
  return mapUsuario(asRecord(data));
}

export async function createUsuario(body: UsuarioDTO): Promise<void> {
  await postJson(urlCadastro("/usuarios"), body);
}

export async function fetchUsuarios(): Promise<UsuarioDTO[]> {
  const data = await getJson(urlCadastro("/usuarios"));
  if (!Array.isArray(data)) return [];
  return data.map((r) => mapUsuario(asRecord(r)));
}

export async function fetchRestaurantes(): Promise<RestauranteDTO[]> {
  const data = await getJson(urlCadastro("/restaurantes"));
  if (!Array.isArray(data)) return [];
  return data.map((r) => mapRestaurante(asRecord(r)));
}

export async function fetchRestaurante(restId: string): Promise<RestauranteDTO> {
  const data = await getJson(urlCadastro(`/restaurantes/${encodeURIComponent(restId)}`));
  return mapRestaurante(asRecord(data));
}

export async function createRestaurante(body: Omit<RestauranteDTO, never>): Promise<void> {
  await postJson(urlCadastro("/restaurantes"), body);
}

export async function fetchEntregadores(): Promise<EntregadorDTO[]> {
  const data = await getJson(urlCadastro("/entregadores"));
  if (!Array.isArray(data)) return [];
  return data.map((r) => mapEntregador(asRecord(r)));
}

export async function fetchEntregador(id: string): Promise<EntregadorDTO> {
  const data = await getJson(urlCadastro(`/entregadores/${encodeURIComponent(id)}`));
  return mapEntregador(asRecord(data));
}

export async function createEntregador(body: EntregadorDTO): Promise<void> {
  await postJson(urlCadastro("/entregadores"), body);
}

export async function createProduto(body: ProdutoCreate): Promise<void> {
  await postJson(urlCadastro("/produtos"), body);
}
