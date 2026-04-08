import { useCallback } from "react";
import {
  createEntregador,
  createProduto,
  createRestaurante,
  createUsuario,
  fetchEntregador,
  fetchRestaurante,
  fetchUsuario,
  type EntregadorDTO,
  type RestauranteDTO,
  type UsuarioDTO,
} from "../api/cadastro";

export function useAdminCadastro() {
  const addUsuario = useCallback((u: UsuarioDTO) => createUsuario(u), []);
  const addRestaurante = useCallback((r: RestauranteDTO) => createRestaurante(r), []);
  const addEntregador = useCallback((e: EntregadorDTO) => createEntregador(e), []);
  const addProduto = useCallback(
    (p: { prod_id: string; nome: string; rest_id: string }) => createProduto(p),
    []
  );
  const getUsuario = useCallback((id: string) => fetchUsuario(id), []);
  const getRestaurante = useCallback((id: string) => fetchRestaurante(id), []);
  const getEntregador = useCallback((id: string) => fetchEntregador(id), []);

  return { addUsuario, addRestaurante, addEntregador, addProduto, getUsuario, getRestaurante, getEntregador };
}
