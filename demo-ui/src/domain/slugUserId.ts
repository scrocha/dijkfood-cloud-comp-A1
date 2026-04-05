/**
 * Converte nome digitado em `user_id` estável para a API de cadastro.
 * Apenas [a-z0-9-]; colisões possíveis se dois nomes normalizam igual.
 */
export function slugUserId(name: string): string {
  const trimmed = name.trim();
  if (!trimmed) return "";

  const nfd = trimmed.normalize("NFD");
  const noLatin1 = nfd.replace(/\p{M}/gu, "");
  const lower = noLatin1.toLowerCase();

  const slug = lower
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 200);

  return slug || "user";
}
