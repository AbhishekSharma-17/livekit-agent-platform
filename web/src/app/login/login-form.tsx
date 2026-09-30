"use client";

import * as React from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { MailIcon } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { busyLabelFor } from "@/components/shared/busy-label";
import { Field } from "@/components/shared/field";
import { PasswordInput } from "@/components/shared/password-input";
import { InputWithIcon } from "@/components/shared/search-field";
import { api } from "@/lib/api";
import { safeNextPath } from "@/lib/auth";

import { SignInCard } from "./sign-in-card";
import { inviteError, loginError, signInErrorText, type SignInError } from "./sign-in-errors";

/**
 * The sign-in card and the invite-acceptance card (same route, different
 * mode by `?invite=`). CONTRACTS-V2 §3.1:
 * `POST /v1/auth/login {email,password}` → 204 + `lkap_session` cookie;
 * `POST /v1/auth/accept-invite {token,password,name?}` → 204 + cookie.
 *
 * Both submit through the console proxy (`api.post`, not a direct fetch to
 * the api) so the `Set-Cookie` lands on the browser's `localhost:3000`
 * origin. On success this does a **hard navigation** (`window.location.assign`,
 * not the router) so `middleware.ts` sees the fresh cookie on the very next
 * request instead of racing a client-side transition against it.
 *
 * States (docs/ui/DESIGN-SYSTEM.md section 8): busy (the button's gerund
 * label, no spinner), error (plain copy plus a next step in a `role="alert"`
 * block; typed values are kept and focus returns to the password), success
 * (the button says where it is going while the page navigates) and offline
 * (a notice that says what will happen).
 *
 * No forgot-password link (docs/ui/AUDIT.md D9): the api has no reset
 * endpoint yet, and a link to nowhere is worse than none.
 */
export function LoginForm() {
  const searchParams = useSearchParams();
  const next = safeNextPath(searchParams.get("next"));
  const inviteToken = searchParams.get("invite");

  return inviteToken ? <AcceptInviteCard token={inviteToken} next={next} /> : <LoginCard next={next} />;
}

type Phase = "idle" | "submitting" | "redirecting";

function subscribeToConnection(onChange: () => void) {
  window.addEventListener("online", onChange);
  window.addEventListener("offline", onChange);
  return () => {
    window.removeEventListener("online", onChange);
    window.removeEventListener("offline", onChange);
  };
}

/** `navigator.onLine`, live; the server (and the first client render) assume online. */
function useOnline(): boolean {
  return React.useSyncExternalStore(
    subscribeToConnection,
    () => navigator.onLine,
    () => true,
  );
}

function OfflineNotice() {
  return (
    <Alert tone="warning" title="You're offline" className="mb-4">
      Signing in needs a connection. You can sign in as soon as it&apos;s back.
    </Alert>
  );
}

function ErrorAlert({ title, error }: { title: string; error: SignInError }) {
  return (
    <Alert tone="danger" title={title} className="mb-4">
      {signInErrorText(error)}
    </Alert>
  );
}

/**
 * Submit handling shared by both cards: phase, the error, and focus back on
 * the password once the form is enabled again (the inputs are disabled while
 * the request runs, so focusing in the same tick would not stick).
 */
function useSignInSubmit(send: () => Promise<void>, toError: (error: unknown) => SignInError, next: string) {
  const [phase, setPhase] = React.useState<Phase>("idle");
  const [error, setError] = React.useState<SignInError | null>(null);
  const passwordRef = React.useRef<HTMLInputElement>(null);

  React.useEffect(() => {
    if (error && phase === "idle") passwordRef.current?.focus();
  }, [error, phase]);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setPhase("submitting");
    setError(null);
    try {
      await send();
      setPhase("redirecting");
      window.location.assign(next);
    } catch (err) {
      setError(toError(err));
      setPhase("idle");
    }
  }

  return { phase, error, onSubmit, passwordRef, busy: phase !== "idle" };
}

function LoginCard({ next }: { next: string }) {
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const online = useOnline();
  const { phase, error, onSubmit, passwordRef, busy } = useSignInSubmit(
    () => api.post("auth/login", { email, password }),
    loginError,
    next,
  );

  return (
    <SignInCard
      title="Sign in"
      description="Sign in to the LKAP console with your workspace email."
      footer={<p>No account yet? Ask a workspace admin for an invite link.</p>}
    >
      {!online ? <OfflineNotice /> : null}
      {error ? <ErrorAlert title="Couldn't sign in" error={error} /> : null}
      <form onSubmit={onSubmit} className="flex flex-col gap-4">
        <Field label="Email" htmlFor="login-email">
          <InputWithIcon
            icon={MailIcon}
            id="login-email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            disabled={busy}
          />
        </Field>
        <Field label="Password" htmlFor="login-password">
          <PasswordInput
            ref={passwordRef}
            id="login-password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            disabled={busy}
          />
        </Field>
        <Button
          type="submit"
          variant="primary"
          size="block"
          className="mt-1"
          busy={busy}
          busyLabel={phase === "redirecting" ? "Opening the console…" : busyLabelFor("Sign in")}
        >
          Sign in
        </Button>
      </form>
    </SignInCard>
  );
}

function AcceptInviteCard({ token, next }: { token: string; next: string }) {
  const [password, setPassword] = React.useState("");
  const [name, setName] = React.useState("");
  const online = useOnline();
  const { phase, error, onSubmit, passwordRef, busy } = useSignInSubmit(
    () => api.post("auth/accept-invite", { token, password, name: name || undefined }),
    inviteError,
    next,
  );

  if (error?.inviteUsed) {
    return (
      <SignInCard title="Invite already used" description="This invite link has already been redeemed.">
        <div className="flex flex-col gap-4">
          <p className="text-label text-text-secondary">
            If you already have an account, sign in instead. Otherwise, ask a workspace admin to send a fresh invite.
          </p>
          <Button asChild variant="primary" size="block">
            <Link href="/login">Sign in</Link>
          </Button>
        </div>
      </SignInCard>
    );
  }

  return (
    <SignInCard title="Accept your invite" description="Set a password to join the workspace.">
      {!online ? <OfflineNotice /> : null}
      {error ? <ErrorAlert title="Couldn't join the workspace" error={error} /> : null}
      <form onSubmit={onSubmit} className="flex flex-col gap-4">
        <Field label="Name" htmlFor="invite-name" optional>
          <Input
            id="invite-name"
            autoComplete="name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            disabled={busy}
          />
        </Field>
        <Field label="Password" htmlFor="invite-password" hint="At least 8 characters.">
          <PasswordInput
            ref={passwordRef}
            id="invite-password"
            autoComplete="new-password"
            required
            minLength={8}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            disabled={busy}
          />
        </Field>
        <Button
          type="submit"
          variant="primary"
          size="block"
          className="mt-1"
          busy={busy}
          busyLabel={phase === "redirecting" ? "Opening the console…" : busyLabelFor("Join workspace")}
        >
          Join workspace
        </Button>
      </form>
    </SignInCard>
  );
}
