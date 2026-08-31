export const PLAID_LINK_TOKEN_STORAGE_KEY = "portfolio-lab:plaid-link-token";

export function isPlaidOAuthReturn(search: string): boolean {
  const stateId = new URLSearchParams(search).get("oauth_state_id");
  return Boolean(stateId?.trim());
}

export function cleanPlaidOAuthReturnUrl(href: string): string {
  const url = new URL(href);
  url.searchParams.delete("oauth_state_id");
  return `${url.pathname}${url.search}${url.hash}`;
}
