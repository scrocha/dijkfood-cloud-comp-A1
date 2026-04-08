/** Itens só para demonstração; não precisam existir na tabela PRODUTOS. */
export type MockProduct = {
  id: string;
  label: string;
  unitPrice: number;
};

export const MOCK_PRODUCTS: MockProduct[] = [
  { id: "demo-marmita", label: "Marmita executiva", unitPrice: 24.9 },
  { id: "demo-burger", label: "Combo hambúrguer", unitPrice: 32.5 },
  { id: "demo-suco", label: "Suco natural 500ml", unitPrice: 9.0 },
  { id: "demo-brownie", label: "Brownie com sorvete", unitPrice: 18.0 },
];
