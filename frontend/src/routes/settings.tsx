import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  Activity,
  Clock3,
  KeyRound,
  LoaderCircle,
  LogOut,
  Mail,
  MonitorSmartphone,
  ShieldCheck,
  Smartphone,
  Trash2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  fetchSessions,
  fetchSecurityEvents,
  loginUrl,
  passwordResetUrl,
  revokeOtherSessions,
  revokeSession,
} from "@/auth/client";
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

      <SessionsPanel />
      <SecurityActivityPanel />
    </div>
  );
}

const securityEventsQueryKey = ["auth", "security-events"] as const;

function SecurityActivityPanel() {
  const eventsQuery = useQuery({
    queryKey: securityEventsQueryKey,
    queryFn: fetchSecurityEvents,
  });

  return (
    <section className="rounded-3xl border border-border bg-bg-subtle p-6 shadow-2xl shadow-black/10">
      <div className="flex items-start gap-3">
        <div className="grid h-10 w-10 place-items-center rounded-2xl bg-accent/10 text-accent">
          <Activity className="h-5 w-5" />
        </div>
        <div>
          <h2 className="font-semibold text-fg-base">
            Recent security activity
          </h2>
          <p className="mt-1 text-sm text-fg-muted">
            Successful authentication and session changes recorded for your
            account.
          </p>
        </div>
      </div>
      <div className="mt-5 space-y-2">
        {eventsQuery.isPending ? (
          <p className="text-sm text-fg-muted">Loading security activity…</p>
        ) : eventsQuery.isError ? (
          <p className="text-sm text-danger">{eventsQuery.error.message}</p>
        ) : eventsQuery.data.length === 0 ? (
          <p className="rounded-2xl border border-border bg-bg-base px-4 py-4 text-sm text-fg-muted">
            No security activity has been recorded yet.
          </p>
        ) : (
          eventsQuery.data.map((event) => (
            <div
              key={event.id}
              className="flex items-center justify-between gap-4 rounded-2xl border border-border bg-bg-base px-4 py-3"
            >
              <div className="flex items-center gap-3">
                <ShieldCheck className="h-4 w-4 text-success" />
                <span className="text-sm font-medium text-fg-base">
                  {securityEventLabel(event.event_type)}
                </span>
              </div>
              <time className="text-xs text-fg-muted">
                {formatSessionDate(event.created_at)}
              </time>
            </div>
          ))
        )}
      </div>
    </section>
  );
}

function securityEventLabel(eventType: string): string {
  return (
    {
      login_succeeded: "Signed in",
      logout_succeeded: "Signed out",
      session_revoked: "Session revoked",
      other_sessions_revoked: "Other sessions revoked",
    }[eventType] ?? "Account security updated"
  );
}

const sessionsQueryKey = ["auth", "sessions"] as const;

function SessionsPanel() {
  const queryClient = useQueryClient();
  const sessionsQuery = useQuery({
    queryKey: sessionsQueryKey,
    queryFn: fetchSessions,
  });
  const refresh = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: sessionsQueryKey }),
      queryClient.invalidateQueries({ queryKey: securityEventsQueryKey }),
    ]);
  const revokeOne = useMutation({
    mutationFn: revokeSession,
    onSuccess: refresh,
  });
  const revokeOthers = useMutation({
    mutationFn: revokeOtherSessions,
    onSuccess: refresh,
  });
  const sessions = sessionsQuery.data ?? [];
  const otherSessionCount = sessions.filter(
    (session) => !session.is_current,
  ).length;
  const mutationError = revokeOne.error ?? revokeOthers.error;

  return (
    <section className="overflow-hidden rounded-3xl border border-border bg-bg-subtle shadow-2xl shadow-black/10">
      <header className="flex flex-wrap items-center justify-between gap-4 border-b border-border px-6 py-5">
        <div className="flex items-start gap-3">
          <div className="grid h-10 w-10 place-items-center rounded-2xl bg-accent/10 text-accent">
            <MonitorSmartphone className="h-5 w-5" />
          </div>
          <div>
            <h2 className="font-semibold text-fg-base">Active sessions</h2>
            <p className="mt-1 text-sm text-fg-muted">
              Review dashboard access and remove devices you no longer use.
            </p>
          </div>
        </div>
        <Button
          variant="outline"
          disabled={otherSessionCount === 0 || revokeOthers.isPending}
          onClick={() => revokeOthers.mutate()}
        >
          {revokeOthers.isPending ? (
            <LoaderCircle className="h-4 w-4 animate-spin" />
          ) : (
            <Trash2 className="h-4 w-4" />
          )}
          Sign out other sessions
        </Button>
      </header>

      <div className="p-6">
        {sessionsQuery.isPending ? (
          <div className="flex items-center gap-3 rounded-2xl border border-border bg-bg-base px-4 py-5 text-sm text-fg-muted">
            <LoaderCircle className="h-4 w-4 animate-spin text-accent" />
            Checking your active sessions…
          </div>
        ) : sessionsQuery.isError ? (
          <div className="rounded-2xl border border-danger/20 bg-danger/5 px-4 py-4 text-sm text-danger">
            {sessionsQuery.error.message}
            <Button
              className="ml-3"
              size="sm"
              variant="outline"
              onClick={() => void sessionsQuery.refetch()}
            >
              Retry
            </Button>
          </div>
        ) : (
          <div className="grid gap-3 md:grid-cols-2">
            {sessions.map((session) => (
              <article
                key={session.id}
                className="rounded-2xl border border-border bg-bg-base p-4"
              >
                <div className="flex items-start justify-between gap-4">
                  <div className="flex items-center gap-3">
                    <div className="grid h-9 w-9 place-items-center rounded-xl bg-bg-elevated text-fg-muted">
                      <MonitorSmartphone className="h-4 w-4" />
                    </div>
                    <div>
                      <div className="flex items-center gap-2">
                        <h3 className="text-sm font-medium text-fg-base">
                          Dashboard session
                        </h3>
                        {session.is_current ? (
                          <span className="rounded-full bg-success/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-success">
                            This device
                          </span>
                        ) : null}
                      </div>
                      <p className="mt-1 font-mono text-[11px] text-fg-subtle">
                        {session.id.slice(0, 8)}
                      </p>
                    </div>
                  </div>
                  {!session.is_current ? (
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={revokeOne.isPending}
                      onClick={() => revokeOne.mutate(session.id)}
                    >
                      Revoke
                    </Button>
                  ) : null}
                </div>
                <div className="mt-4 flex flex-wrap gap-x-5 gap-y-2 text-xs text-fg-muted">
                  <span className="inline-flex items-center gap-1.5">
                    <Clock3 className="h-3.5 w-3.5" />
                    Started {formatSessionDate(session.created_at)}
                  </span>
                  <span>Expires {formatSessionDate(session.expires_at)}</span>
                </div>
              </article>
            ))}
          </div>
        )}

        {mutationError ? (
          <p className="mt-4 rounded-2xl border border-danger/20 bg-danger/5 px-4 py-3 text-sm text-danger">
            {mutationError.message}
          </p>
        ) : null}
      </div>
    </section>
  );
}

function formatSessionDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
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
