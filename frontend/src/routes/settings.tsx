import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  KeyRound,
  LogOut,
  Mail,
  ShieldCheck,
  Smartphone,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { loginUrl, passwordResetUrl } from "@/auth/client";
import { useAuth } from "@/auth/auth-context";
import { useCurrentUser } from "@/auth/useCurrentUser";

export function SettingsPage() {
  const user = useCurrentUser();
  const { signOut, signingOut, error } = useAuth();

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.22em] text-accent">
            Account security
          </p>
          <h1 className="mt-2 text-3xl font-semibold tracking-tight text-fg-base">
            Access and identity settings
          </h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-fg-muted">
            Manage how you sign in, recover your account, and complete MFA
            enrollment for your dashboard access.
          </p>
        </div>
        <Button variant="outline" asChild>
          <Link to="/symbol">
            Go to workspace
            <ArrowRight className="h-4 w-4" />
          </Link>
        </Button>
      </header>

      <section className="grid gap-4 lg:grid-cols-[1.1fr_0.9fr]">
        <article className="rounded-3xl border border-border bg-bg-subtle p-6 shadow-2xl shadow-black/10">
          <div className="flex items-start gap-4">
            <div className="grid h-12 w-12 place-items-center rounded-2xl bg-accent/10 text-accent">
              <ShieldCheck className="h-5 w-5" />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-fg-base">
                Active identity
              </h2>
              <p className="mt-1 text-sm text-fg-muted">
                Signed in as {user.displayName}
                {user.email ? ` (${user.email})` : ""}
              </p>
            </div>
          </div>

          <div className="mt-6 grid gap-3 sm:grid-cols-2">
            <ActionCard
              icon={<Mail className="h-4 w-4" />}
              title="Email sign-in"
              body="Use Cognito managed login for email and password access."
              href={loginUrl("/app/settings")}
              cta="Open sign-in"
            />
            <ActionCard
              icon={<KeyRound className="h-4 w-4" />}
              title="Password reset"
              body="Send yourself through the managed reset flow without exposing credentials to the app."
              href={passwordResetUrl()}
              cta="Reset password"
            />
            <ActionCard
              icon={<Smartphone className="h-4 w-4" />}
              title="MFA enrollment"
              body="Optional TOTP MFA is enabled in Cognito. Re-authenticate with email to enroll or complete a challenge."
              href={loginUrl("/app/settings")}
              cta="Manage MFA"
            />
            <div className="rounded-2xl border border-border bg-bg-base p-4">
              <div className="flex items-center gap-2 text-sm font-medium text-fg-base">
                <LogOut className="h-4 w-4 text-fg-muted" />
                Sign out
              </div>
              <p className="mt-2 text-sm leading-6 text-fg-muted">
                Revoke the current dashboard session and return to the login
                screen.
              </p>
              <Button
                className="mt-4 w-full"
                variant="outline"
                disabled={signingOut}
                onClick={() => void signOut().catch(() => undefined)}
              >
                {signingOut ? "Signing out…" : "Sign out now"}
              </Button>
            </div>
          </div>

          {error ? (
            <p className="mt-4 rounded-2xl border border-danger/20 bg-danger/5 px-4 py-3 text-sm text-danger">
              {error}
            </p>
          ) : null}
        </article>

        <aside className="rounded-3xl border border-border bg-bg-subtle p-6 shadow-2xl shadow-black/10">
          <h2 className="text-lg font-semibold text-fg-base">How this works</h2>
          <div className="mt-4 space-y-4 text-sm leading-6 text-fg-muted">
            <p>
              Credentials, password recovery, and TOTP verification live in
              Cognito. StockAlert only stores your local user profile, tenant
              membership, and revocable app session in PostgreSQL.
            </p>
            <p>
              For local development, keep the app and logout redirect on the
              same origin so browser cookies clear cleanly and the post-logout
              landing page matches the running UI.
            </p>
            <p>
              Google sign-in and email/password sign-in both land back on the
              same protected dashboard after Cognito completes the managed flow.
            </p>
          </div>
        </aside>
      </section>
    </div>
  );
}

function ActionCard({
  icon,
  title,
  body,
  href,
  cta,
}: {
  icon: ReactNode;
  title: string;
  body: string;
  href: string;
  cta: string;
}) {
  return (
    <a
      href={href}
      className="rounded-2xl border border-border bg-bg-base p-4 transition hover:-translate-y-0.5 hover:border-accent/40 hover:bg-bg-elevated"
    >
      <div className="flex items-center gap-2 text-sm font-medium text-fg-base">
        <span className="text-accent">{icon}</span>
        {title}
      </div>
      <p className="mt-2 text-sm leading-6 text-fg-muted">{body}</p>
      <div className="mt-4 inline-flex items-center gap-2 text-sm font-medium text-accent">
        {cta}
        <ArrowRight className="h-4 w-4" />
      </div>
    </a>
  );
}
