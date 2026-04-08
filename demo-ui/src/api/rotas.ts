import { urlRotas } from "../lib/env";
import { postJson } from "../lib/http";
import { asRecord } from "../lib/record";

export type Ponto = { lat: number; lon: number };

export type RotaEntregaResponse = {
  restaurante_solicitado: Ponto;
  cliente_solicitado: Ponto;
  dados_rota: {
    distancia_metros: number;
    nos: number;
    percursos: Array<{
      ponto_origem: { lat: number; lon: number };
      ponto_fim: { lat: number; lon: number };
      comprimento: number;
    }>;
    origem_projetada: Ponto;
    destino_projetado: Ponto;
  };
};

function toNum(v: unknown): number {
  if (typeof v === "number") return v;
  if (typeof v === "string") return Number(v);
  return NaN;
}

function mapPonto(v: unknown): Ponto {
  const o = asRecord(v);
  return {
    lat: toNum(o.lat),
    lon: toNum(o.lon ?? o.lng),
  };
}

export async function postRotaEntrega(origem: Ponto, destino: Ponto): Promise<RotaEntregaResponse> {
  const raw = await postJson(urlRotas("/rota-entrega"), { origem, destino });
  const r = asRecord(raw);
  const dados = asRecord(r.dados_rota);
  const percursosRaw = dados.percursos;
  const percursos = Array.isArray(percursosRaw)
    ? percursosRaw.map((p) => {
        const seg = asRecord(p);
        return {
          ponto_origem: mapPonto(seg.ponto_origem),
          ponto_fim: mapPonto(seg.ponto_fim),
          comprimento: toNum(seg.comprimento),
        };
      })
    : [];

  return {
    restaurante_solicitado: mapPonto(r.restaurante_solicitado),
    cliente_solicitado: mapPonto(r.cliente_solicitado),
    dados_rota: {
      distancia_metros: toNum(dados.distancia_metros),
      nos: toNum(dados.nos),
      percursos,
      origem_projetada: mapPonto(dados.origem_projetada),
      destino_projetado: mapPonto(dados.destino_projetado),
    },
  };
}
