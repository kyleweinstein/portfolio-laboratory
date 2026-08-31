import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  cleanPlaidOAuthReturnUrl,
  isPlaidOAuthReturn,
  PLAID_LINK_TOKEN_STORAGE_KEY,
} from "../app/plaid-oauth-resume.ts";

test("Plaid OAuth return detection requires a non-empty state id", () => {
  assert.equal(isPlaidOAuthReturn("?oauth_state_id=state-123"), true);
  assert.equal(isPlaidOAuthReturn("?oauth_state_id=%20%20"), false);
  assert.equal(isPlaidOAuthReturn("?source=plaid"), false);
});

test("Plaid OAuth cleanup removes only the OAuth state parameter", () => {
  assert.equal(
    cleanPlaidOAuthReturnUrl("https://lab.example/manage?source=plaid&oauth_state_id=state-123#connections"),
    "/manage?source=plaid#connections",
  );
});

test("the owner UI resumes Plaid Link without storing an access token", () => {
  const source = readFileSync(new URL("../app/manage-providers.tsx", import.meta.url), "utf8");
  assert.equal(PLAID_LINK_TOKEN_STORAGE_KEY, "portfolio-lab:plaid-link-token");
  assert.match(source, /sessionStorage\.setItem\(PLAID_LINK_TOKEN_STORAGE_KEY, linkToken\)/);
  assert.match(source, /openPlaidLink\(linkToken, window\.location\.href\)/);
  assert.match(source, /sessionStorage\.removeItem\(PLAID_LINK_TOKEN_STORAGE_KEY\)/);
  assert.doesNotMatch(source, /sessionStorage\.(?:setItem|getItem)\([^\n]*(?:access|public)[_-]?token/i);
});
