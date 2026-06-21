import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import QRCode from "qrcode";
import {
  ArrowRight,
  Activity,
  CheckCircle2,
  Clock3,
  CreditCard,
  KeyRound,
  LoaderCircle,
  LogOut,
  Mail,
  MonitorSmartphone,
  ShieldAlert,
  ShieldCheck,
  Smartphone,
  Sparkles,
  Trash2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  AuthRequestError,
  beginMfaEnrollment,
  fetchBillingStatus,
  fetchMfaStatus,
  fetchSessions,
  fetchSecurityEvents,
  loginUrl,
  openBillingPortal,
  passwordResetUrl,
  revokeOtherSessions,
  revokeSession,
  startCheckout,
  verifyMfaEnrollment,
  type MfaEnrollment,
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

      <BillingPanel />
      <MfaPanel />
      <SessionsPanel />
      <SecurityActivityPanel />
    </div>
  );
}

const billingStatusQueryKey = ["billing", "status"] as const;

function BillingPanel() {
  const queryClient = useQueryClient();
  const statusQuery = useQuery({
    queryKey: billingStatusQueryKey,
    queryFn: fetchBillingStatus,
  });

  // Surface the Stripe redirect result and refresh (the webhook may lag a beat).
  const [banner, setBanner] = useState<"success" | "canceled" | null>(null);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const outcome = params.get("billing");
    if (outcome === "success" || outcome === "canceled") {
      setBanner(outcome);
      params.delete("billing");
      const qs = params.toString();
      window.history.replaceState(
        {},
        "",
        window.location.pathname + (qs ? `?${qs}` : ""),
      );
      if (outcome === "success") {
        void queryClient.invalidateQueries({ queryKey: billingStatusQueryKey });
      }
    }
  }, [queryClient]);

  const checkout = useMutation({
    mutationFn: startCheckout,
    onSuccess: (url) => {
      window.location.href = url;
    },
  });
  const portal = useMutation({
    mutationFn: openBillingPortal,
    onSuccess: (url) => {
      window.location.href = url;
    },
  });

  const status = statusQuery.data;
  const mutationError = checkout.error ?? portal.error;
  const redirecting = checkout.isPending || portal.isPending;

  return (
    <section className="rounded-3xl border border-border bg-bg-subtle p-6 shadow-2xl shadow-black/10">
      <div className="flex items-start gap-3">
        <div className="grid h-10 w-10 place-items-center rounded-2xl bg-accent/10 text-accent">
          <CreditCard className="h-5 w-5" />
        </div>
        <div>
          <h2 className="font-semibold text-fg-base">Subscription</h2>
          <p className="mt-1 text-sm text-fg-muted">
            Manage your StockAlert plan, billing, and invoices.
          </p>
        </div>
      </div>

      {banner ? (
        <p
          className={
            banner === "success"
              ? "mt-4 rounded-2xl border border-success/20 bg-success/5 px-4 py-3 text-sm text-success"
              : "mt-4 rounded-2xl border border-border bg-bg-base px-4 py-3 text-sm text-fg-muted"
          }
        >
          {banner === "success"
            ? "Thanks! Your subscription is being activated — it may take a moment to appear."
            : "Checkout canceled — no charge was made."}
        </p>
      ) : null}

      <div className="mt-5">
        {statusQuery.isPending ? (
          <div className="flex items-center gap-3 rounded-2xl border border-border bg-bg-base px-4 py-5 text-sm text-fg-muted">
            <LoaderCircle className="h-4 w-4 animate-spin text-accent" />
            Checking your subscription…
          </div>
        ) : statusQuery.isError ? (
          <PanelError
            message={statusQuery.error.message}
            onRetry={() => void statusQuery.refetch()}
          />
        ) : status?.active ? (
          <ActiveSubscription
            status={status}
            onManage={() => portal.mutate()}
            managing={portal.isPending}
          />
        ) : (
          <PlanChooser
            onSelect={(plan) => checkout.mutate(plan)}
            busy={redirecting}
          />
        )}

        {mutationError ? (
          <p className="mt-4 rounded-2xl border border-danger/20 bg-danger/5 px-4 py-3 text-sm text-danger">
            {mutationError instanceof Error
              ? mutationError.message
              : "Something went wrong."}
          </p>
        ) : null}
      </div>
    </section>
  );
}

function ActiveSubscription({
  status,
  onManage,
  managing,
}: {
  status: {
    status: string;
    plan?: string | null;
    current_period_end?: string | null;
    cancel_at_period_end: boolean;
  };
  onManage: () => void;
  managing: boolean;
}) {
  const trialing = status.status === "trialing";
  const periodLabel = status.cancel_at_period_end ? "Ends" : "Renews";
  return (
    <div className="rounded-2xl border border-border bg-bg-base p-5">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <CheckCircle2 className="h-5 w-5 text-success" />
          <div>
            <p className="text-sm font-medium text-fg-base">
              StockAlert Pro
              <span className="ml-2 rounded-full bg-success/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-success">
                {trialing ? "Trial" : "Active"}
              </span>
            </p>
            <p className="mt-1 text-xs text-fg-muted">
              {status.plan ? `${status.plan} billing` : "Pro plan"}
              {status.current_period_end
                ? ` · ${periodLabel} ${formatSessionDate(status.current_period_end)}`
                : ""}
            </p>
          </div>
        </div>
        <Button variant="outline" disabled={managing} onClick={onManage}>
          {managing ? (
            <LoaderCircle className="h-4 w-4 animate-spin" />
          ) : (
            <CreditCard className="h-4 w-4" />
          )}
          Manage billing
        </Button>
      </div>
      {status.cancel_at_period_end ? (
        <p className="mt-4 text-sm text-fg-muted">
          Your plan is set to cancel at the end of the current period. You can
          resume it from the billing portal.
        </p>
      ) : null}
    </div>
  );
}

function PlanChooser({
  onSelect,
  busy,
}: {
  onSelect: (plan: "monthly" | "annual") => void;
  busy: boolean;
}) {
  return (
    <div className="rounded-2xl border border-border bg-bg-base p-5">
      <div className="flex items-center gap-2 text-sm font-medium text-fg-base">
        <Sparkles className="h-4 w-4 text-accent" />
        You're on the Free plan
      </div>
      <p className="mt-2 text-sm leading-6 text-fg-muted">
        Upgrade to Pro for full access to alerts, backtesting, and simulated
        trading. Includes a 14-day free trial.
      </p>
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <button
          type="button"
          disabled={busy}
          onClick={() => onSelect("monthly")}
          className="rounded-2xl border border-border bg-bg-subtle p-4 text-left transition hover:-translate-y-0.5 hover:border-accent/40 disabled:pointer-events-none disabled:opacity-50"
        >
          <div className="text-sm font-medium text-fg-base">Pro Monthly</div>
          <div className="mt-1 text-2xl font-semibold text-fg-base">
            $29<span className="text-sm font-normal text-fg-muted">/mo</span>
          </div>
          <div className="mt-3 inline-flex items-center gap-2 text-sm font-medium text-accent">
            Start trial <ArrowRight className="h-4 w-4" />
          </div>
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => onSelect("annual")}
          className="rounded-2xl border border-accent/40 bg-accent/5 p-4 text-left transition hover:-translate-y-0.5 hover:border-accent disabled:pointer-events-none disabled:opacity-50"
        >
          <div className="flex items-center gap-2 text-sm font-medium text-fg-base">
            Pro Annual
            <span className="rounded-full bg-accent/15 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-accent">
              2 months free
            </span>
          </div>
          <div className="mt-1 text-2xl font-semibold text-fg-base">
            $290<span className="text-sm font-normal text-fg-muted">/yr</span>
          </div>
          <div className="mt-3 inline-flex items-center gap-2 text-sm font-medium text-accent">
            Start trial <ArrowRight className="h-4 w-4" />
          </div>
        </button>
      </div>
      {busy ? (
        <p className="mt-3 flex items-center gap-2 text-sm text-fg-muted">
          <LoaderCircle className="h-4 w-4 animate-spin text-accent" />
          Redirecting to secure checkout…
        </p>
      ) : null}
    </div>
  );
}

const mfaStatusQueryKey = ["auth", "mfa-status"] as const;

function MfaPanel() {
  const queryClient = useQueryClient();
  const statusQuery = useQuery({
    queryKey: mfaStatusQueryKey,
    queryFn: fetchMfaStatus,
  });
  const [enrollment, setEnrollment] = useState<MfaEnrollment | null>(null);
  const [code, setCode] = useState("");

  const begin = useMutation({
    mutationFn: beginMfaEnrollment,
    onSuccess: (data) => {
      setEnrollment(data);
      setCode("");
    },
  });
  const verify = useMutation({
    mutationFn: () => verifyMfaEnrollment(code),
    onSuccess: async () => {
      setEnrollment(null);
      setCode("");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: mfaStatusQueryKey }),
        queryClient.invalidateQueries({ queryKey: securityEventsQueryKey }),
      ]);
    },
  });

  const onVerify = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (code.length === 6) verify.mutate();
  };

  const status = statusQuery.data;
  const beginError = begin.error;
  const reauthRequired =
    status?.reauthentication_required ||
    (beginError instanceof AuthRequestError &&
      beginError.code === "reauthentication_required");

  return (
    <section className="rounded-3xl border border-border bg-bg-subtle p-6 shadow-2xl shadow-black/10">
      <div className="flex items-start gap-3">
        <div className="grid h-10 w-10 place-items-center rounded-2xl bg-accent/10 text-accent">
          <Smartphone className="h-5 w-5" />
        </div>
        <div>
          <h2 className="font-semibold text-fg-base">
            Authenticator app (TOTP)
          </h2>
          <p className="mt-1 text-sm text-fg-muted">
            Add a time-based one-time passcode from an app like 1Password,
            Authy, or Google Authenticator.
          </p>
        </div>
      </div>

      <div className="mt-5">
        {statusQuery.isPending ? (
          <div className="flex items-center gap-3 rounded-2xl border border-border bg-bg-base px-4 py-5 text-sm text-fg-muted">
            <LoaderCircle className="h-4 w-4 animate-spin text-accent" />
            Checking your MFA status…
          </div>
        ) : statusQuery.isError ? (
          <PanelError
            message={statusQuery.error.message}
            onRetry={() => void statusQuery.refetch()}
          />
        ) : status && !status.supported ? (
          <InfoRow
            icon={<ShieldAlert className="h-4 w-4 text-fg-muted" />}
            text="MFA for this account is managed by your external identity provider (e.g. Google). Enroll or update it from that provider."
          />
        ) : reauthRequired ? (
          <div className="rounded-2xl border border-border bg-bg-base px-4 py-4">
            <InfoRow
              icon={<ShieldAlert className="h-4 w-4 text-warning" />}
              text="Managing MFA requires a recent sign-in. Re-authenticate to continue."
            />
            <Button className="mt-4" variant="outline" asChild>
              <a href={loginUrl("/app/settings")}>
                Re-authenticate
                <ArrowRight className="h-4 w-4" />
              </a>
            </Button>
          </div>
        ) : status?.enabled ? (
          <InfoRow
            icon={<CheckCircle2 className="h-4 w-4 text-success" />}
            text="An authenticator app is enrolled and active for your account."
          />
        ) : enrollment ? (
          <MfaEnrollmentForm
            enrollment={enrollment}
            code={code}
            onCodeChange={setCode}
            onSubmit={onVerify}
            verifying={verify.isPending}
            error={verify.error instanceof Error ? verify.error.message : null}
            onCancel={() => {
              setEnrollment(null);
              setCode("");
              verify.reset();
            }}
          />
        ) : (
          <div className="rounded-2xl border border-border bg-bg-base px-4 py-4">
            <InfoRow
              icon={<ShieldCheck className="h-4 w-4 text-fg-muted" />}
              text="No authenticator app is enrolled yet. Set one up to add a second factor to your sign-in."
            />
            <Button
              className="mt-4"
              disabled={begin.isPending}
              onClick={() => begin.mutate()}
            >
              {begin.isPending ? (
                <LoaderCircle className="h-4 w-4 animate-spin" />
              ) : (
                <Smartphone className="h-4 w-4" />
              )}
              Set up authenticator
            </Button>
            {beginError && !reauthRequired ? (
              <p className="mt-3 text-sm text-danger">
                {beginError instanceof Error
                  ? beginError.message
                  : "We couldn't start MFA enrollment."}
              </p>
            ) : null}
          </div>
        )}
      </div>
    </section>
  );
}

function MfaEnrollmentForm({
  enrollment,
  code,
  onCodeChange,
  onSubmit,
  verifying,
  error,
  onCancel,
}: {
  enrollment: MfaEnrollment;
  code: string;
  onCodeChange: (value: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  verifying: boolean;
  error: string | null;
  onCancel: () => void;
}) {
  const [qrDataUrl, setQrDataUrl] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    QRCode.toDataURL(enrollment.otpauth_uri, { margin: 1, width: 200 })
      .then((url) => {
        if (active) setQrDataUrl(url);
      })
      .catch(() => {
        if (active) setQrDataUrl(null);
      });
    return () => {
      active = false;
    };
  }, [enrollment.otpauth_uri]);

  return (
    <div className="rounded-2xl border border-border bg-bg-base p-5">
      <div className="grid gap-6 sm:grid-cols-[auto_1fr]">
        <div className="flex flex-col items-center gap-3">
          {qrDataUrl ? (
            <img
              src={qrDataUrl}
              alt="Authenticator setup QR code"
              className="h-44 w-44 rounded-xl border border-border bg-white p-2"
            />
          ) : (
            <div className="grid h-44 w-44 place-items-center rounded-xl border border-border text-sm text-fg-muted">
              <LoaderCircle className="h-5 w-5 animate-spin text-accent" />
            </div>
          )}
          <div className="text-center">
            <p className="text-xs text-fg-muted">Or enter this key manually</p>
            <code className="mt-1 block break-all font-mono text-xs text-fg-base">
              {enrollment.secret_code}
            </code>
          </div>
        </div>

        <form onSubmit={onSubmit} className="flex flex-col gap-3">
          <div>
            <p className="text-sm font-medium text-fg-base">
              Scan, then enter the 6-digit code
            </p>
            <p className="mt-1 text-sm text-fg-muted">
              Scan the QR code with your authenticator app and type the code it
              shows to finish enrollment.
            </p>
          </div>
          <input
            inputMode="numeric"
            autoComplete="one-time-code"
            pattern="[0-9]{6}"
            maxLength={6}
            placeholder="123456"
            value={code}
            onChange={(event) =>
              onCodeChange(event.target.value.replace(/\D/g, "").slice(0, 6))
            }
            className="w-40 rounded-lg border border-border bg-bg-subtle px-3 py-2 font-mono text-lg tracking-[0.4em] text-fg-base outline-none focus:border-accent focus:ring-2 focus:ring-accent/40"
          />
          {error ? <p className="text-sm text-danger">{error}</p> : null}
          <div className="mt-1 flex gap-2">
            <Button type="submit" disabled={verifying || code.length !== 6}>
              {verifying ? (
                <LoaderCircle className="h-4 w-4 animate-spin" />
              ) : (
                <CheckCircle2 className="h-4 w-4" />
              )}
              Verify and enable
            </Button>
            <Button type="button" variant="ghost" onClick={onCancel}>
              Cancel
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}

function InfoRow({ icon, text }: { icon: ReactNode; text: string }) {
  return (
    <div className="flex items-start gap-2 text-sm text-fg-muted">
      <span className="mt-0.5">{icon}</span>
      <span>{text}</span>
    </div>
  );
}

function PanelError({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div className="rounded-2xl border border-danger/20 bg-danger/5 px-4 py-4 text-sm text-danger">
      {message}
      <Button className="ml-3" size="sm" variant="outline" onClick={onRetry}>
        Retry
      </Button>
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
      mfa_enabled: "MFA enabled",
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
