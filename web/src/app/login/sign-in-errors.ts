import { friendlyError } from "@/components/console/lib/friendly-error";
import { ApiError } from "@/lib/api";

/**
 * Sign-in and invite errors in plain words (docs/ui/DESIGN-SYSTEM.md
 * sections 3 and 8.4, docs/ui/AUDIT.md S1). A thin wrapper over
 * `friendlyError`: the shared mapper reads a 401 as "Your session has ended",
 * which is wrong on the page where you sign in, so the auth statuses get their
 * own copy here and everything else (network failures, validation) falls
 * through to it.
 *
 * The api answers a wrong email and a wrong password with the same 401
 * (`routers/auth.py` `_INVALID_LOGIN`), and this copy keeps it that way: it
 * never says which of the two was wrong.
 */
export interface SignInError {
  /** What happened, one plain sentence. */
  detail: string;
  /** What to do next, one sentence. */
  nextStep: string | null;
  /** The invite has been redeemed already: show the "Invite already used" screen. */
  inviteUsed?: boolean;
}

const SERVICE_DOWN: SignInError = {
  detail: "The sign-in service isn't responding.",
  nextStep: "Try again in a moment. If it keeps happening, ask an admin to check the API.",
};

const TOO_MANY: SignInError = {
  detail: "There have been too many sign-in attempts.",
  nextStep: "Wait a minute, then try again.",
};

function fallback(error: unknown): SignInError {
  const friendly = friendlyError(error);
  return { detail: friendly.detail, nextStep: friendly.nextStep };
}

function serverSide(error: unknown): SignInError | null {
  if (!(error instanceof ApiError)) return null;
  if (error.status >= 500) return SERVICE_DOWN;
  if (error.status === 429) return TOO_MANY;
  return null;
}

/** `POST auth/login` failures. */
export function loginError(error: unknown): SignInError {
  const shared = serverSide(error);
  if (shared) return shared;
  if (error instanceof ApiError) {
    if (error.status === 401) {
      return { detail: "That email and password don't match.", nextStep: "Check them and try again." };
    }
    if (error.status === 422) {
      return { detail: "That doesn't look like an email address.", nextStep: "Check it and try again." };
    }
  }
  return fallback(error);
}

/** `POST auth/accept-invite` failures (`routers/auth.py` `accept_invite`). */
export function inviteError(error: unknown): SignInError {
  const shared = serverSide(error);
  if (shared) return shared;
  if (error instanceof ApiError) {
    const message = error.message.toLowerCase();
    if (error.status === 400 && message.includes("already been used")) {
      return {
        detail: "This invite link has already been used.",
        nextStep: "Sign in instead, or ask a workspace admin for a new invite.",
        inviteUsed: true,
      };
    }
    if (error.status === 400 && message.includes("expired")) {
      return { detail: "This invite link has expired.", nextStep: "Ask a workspace admin to send a new invite." };
    }
    if (error.status === 400 && message.startsWith("invalid invite")) {
      return {
        detail: "This invite link isn't valid.",
        nextStep: "Check that you opened the whole link, or ask a workspace admin for a new invite.",
      };
    }
    if (error.status === 401) {
      return {
        detail: "That password didn't work.",
        nextStep: "If you already have an account, enter the password you sign in with.",
      };
    }
  }
  // 422 (password too short or too long) is authored plain copy; the shared mapper shows it.
  return fallback(error);
}

/** The text to show: the detail, then the next step. */
export function signInErrorText(error: SignInError): string {
  return error.nextStep ? `${error.detail} ${error.nextStep}` : error.detail;
}
