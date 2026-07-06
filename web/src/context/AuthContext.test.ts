import { describe, expect, it } from "vitest";
import { shouldRemoveIdentityAfterVerifyError } from "./AuthContext";

describe("shouldRemoveIdentityAfterVerifyError", () => {
  it("removes stored identities only for auth failures", () => {
    expect(shouldRemoveIdentityAfterVerifyError({ response: { status: 401 } })).toBe(true);
    expect(shouldRemoveIdentityAfterVerifyError({ response: { status: 403 } })).toBe(true);
  });

  it("keeps stored identities for transient API failures", () => {
    expect(shouldRemoveIdentityAfterVerifyError({ response: { status: 500 } })).toBe(false);
    expect(shouldRemoveIdentityAfterVerifyError({ code: "ERR_NETWORK" })).toBe(false);
    expect(shouldRemoveIdentityAfterVerifyError(new Error("Network Error"))).toBe(false);
  });
});
