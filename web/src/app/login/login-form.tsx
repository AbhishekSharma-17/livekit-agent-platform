"use client";

import * as React from "react";
import { useSearchParams } from "next/navigation";
import { KeyRoundIcon, LoaderCircleIcon, MailIcon } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Field } from "@/components/shared/field";
import { Icon } from "@/components/shared/icon";
import { ApiError, api } from "@/lib/api";
import { safeNextPath } from "@/lib/auth";

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
 */
export function LoginForm() {
  const searchParams = useSearchParams();
  const next = safeNextPath(searchParams.get("next"));
  const inviteToken = searchParams.get("invite");

  return inviteToken ? <AcceptInviteCard token={inviteToken} next={next} /> : <LoginCard next={next} />;
}

function CardShell({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <div className="w-full max-w-sm space-y-6">
      <div className="space-y-1.5 text-center">
        <h1 className="font-heading text-xl font-semibold text-foreground">{title}</h1>
        <p className="text-sm text-pretty text-muted-foreground">{description}</p>
      </div>
      {children}
    </div>
  );
}

function ErrorAlert({ message }: { message: string }) {
  return (
    <Alert variant="danger">
      <AlertTitle>Couldn&apos;t sign in</AlertTitle>
      <AlertDescription>{message}</AlertDescription>
    </Alert>
  );
}

function submitErrorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return "Something went wrong. Try again.";
}

function LoginCard({ next }: { next: string }) {
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await api.post("auth/login", { email, password });
      window.location.assign(next);
    } catch (err) {
      setError(submitErrorMessage(err));
      setSubmitting(false);
    }
  }

  return (
    <CardShell title="Sign in" description="Sign in to the LKAP console with your workspace email.">
      <form onSubmit={onSubmit} className="space-y-4 rounded-lg border border-border bg-card p-6 shadow-sm">
        {error ? <ErrorAlert message={error} /> : null}
        <Field label="Email" htmlFor="login-email" required>
          <div className="relative">
            <Icon
              as={MailIcon}
              size="sm"
              className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-muted-foreground"
            />
            <Input
              id="login-email"
              aria-describedby="login-email-hint"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              className="pl-8"
              disabled={submitting}
            />
          </div>
        </Field>
        <Field label="Password" htmlFor="login-password" required>
          <div className="relative">
            <Icon
              as={KeyRoundIcon}
              size="sm"
              className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-muted-foreground"
            />
            <Input
              id="login-password"
              aria-describedby="login-password-hint"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className="pl-8"
              disabled={submitting}
            />
          </div>
        </Field>
        <Button type="submit" className="w-full" disabled={submitting}>
          {submitting ? <Icon as={LoaderCircleIcon} size="sm" className="animate-spin" /> : null}
          Sign in
        </Button>
      </form>
      <p className="text-center text-xs text-pretty text-muted-foreground">
        No account yet? Ask a workspace admin for an invite link.
      </p>
    </CardShell>
  );
}

function AcceptInviteCard({ token, next }: { token: string; next: string }) {
  const [password, setPassword] = React.useState("");
  const [name, setName] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [usedUp, setUsedUp] = React.useState(false);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await api.post("auth/accept-invite", { token, password, name: name || undefined });
      window.location.assign(next);
    } catch (err) {
      if (err instanceof ApiError && err.message.toLowerCase().includes("already been used")) {
        setUsedUp(true);
      }
      setError(submitErrorMessage(err));
      setSubmitting(false);
    }
  }

  if (usedUp) {
    return (
      <CardShell title="Invite already used" description="This invite link has already been redeemed.">
        <Alert variant="warning">
          <AlertDescription>
            If you already have an account, <a href="/login" className="font-medium underline">sign in</a>{" "}
            instead. Otherwise, ask a workspace admin to send a fresh invite.
          </AlertDescription>
        </Alert>
      </CardShell>
    );
  }

  return (
    <CardShell title="Accept your invite" description="Set a password to join the workspace.">
      <form onSubmit={onSubmit} className="space-y-4 rounded-lg border border-border bg-card p-6 shadow-sm">
        {error ? <ErrorAlert message={error} /> : null}
        <Field label="Name" htmlFor="invite-name" optional>
          <Input
            id="invite-name"
            autoComplete="name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            disabled={submitting}
          />
        </Field>
        <Field label="Password" htmlFor="invite-password" required hint="At least 8 characters.">
          <Input
            id="invite-password"
            type="password"
            autoComplete="new-password"
            required
            minLength={8}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            disabled={submitting}
          />
        </Field>
        <Button type="submit" className="w-full" disabled={submitting}>
          {submitting ? <Icon as={LoaderCircleIcon} size="sm" className="animate-spin" /> : null}
          Join workspace
        </Button>
      </form>
    </CardShell>
  );
}
