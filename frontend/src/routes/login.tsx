import {
  Activity,
  ArrowRight,
  BellRing,
  CheckCircle2,
  KeyRound,
  LockKeyhole,
  Mail,
  ShieldCheck,
  Sparkles,
  TrendingUp,
} from "lucide-react";
import { Navigate, useSearchParams } from "react-router-dom";
import { useAuth } from "@/auth/auth-context";
import { loginUrl, passwordResetUrl } from "@/auth/client";
import { branding } from "@/branding";

function GoogleMark() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden className="h-4 w-4">
      <path
        fill="#4285F4"
        d="M21.6 12.23c0-.71-.06-1.4-.18-2.06H12v3.9h5.38a4.6 4.6 0 0 1-2 3.02v2.53h3.24c1.9-1.75 2.98-4.33 2.98-7.39Z"
      />
      <path
        fill="#34A853"
        d="M12 22c2.7 0 4.98-.9 6.63-2.39l-3.25-2.52c-.9.6-2.05.96-3.38.96-2.61 0-4.82-1.76-5.61-4.13H3.04v2.6A10 10 0 0 0 12 22Z"
      />
      <path
        fill="#FBBC05"
        d="M6.39 13.92A6 6 0 0 1 6.08 12c0-.67.12-1.32.31-1.92v-2.6H3.04A10 10 0 0 0 2 12c0 1.61.39 3.14 1.04 4.52l3.35-2.6Z"
      />
      <path
        fill="#EA4335"
        d="M12 5.95c1.47 0 2.79.5 3.82 1.5l2.88-2.88A9.64 9.64 0 0 0 12 2a10 10 0 0 0-8.96 5.48l3.35 2.6C7.18 7.71 9.39 5.95 12 5.95Z"
      />
    </svg>
  );
}

function SignalCanvas() {
  return (
    <div className="relative mt-10 overflow-hidden rounded-3xl border border-white/10 bg-black/20 p-5 shadow-2xl shadow-black/20 backdrop-blur-sm">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.22em] text-white/45">
            Live signal confidence
          </p>
          <p className="mt-1 text-sm font-medium text-white/90">
            Momentum convergence
          </p>
        </div>
        <div className="flex items-center gap-2 rounded-full bg-emerald-400/10 px-2.5 py-1 text-[10px] font-semibold text-emerald-300">
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-300" />{" "}
          Live
        </div>
      </div>
      <svg
        viewBox="0 0 600 170"
        className="mt-4 w-full"
        role="img"
        aria-label="Rising market signal visualization"
      >
        <defs>
          <linearGradient id="signal-line" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0" stopColor="#818cf8" stopOpacity=".35" />
            <stop offset=".55" stopColor="#a78bfa" />
            <stop offset="1" stopColor="#34d399" />
          </linearGradient>
          <linearGradient id="signal-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#8b5cf6" stopOpacity=".24" />
            <stop offset="1" stopColor="#8b5cf6" stopOpacity="0" />
          </linearGradient>
        </defs>
        {[35, 75, 115, 155].map((y) => (
          <line
            key={y}
            x1="0"
            x2="600"
            y1={y}
            y2={y}
            stroke="white"
            strokeOpacity=".07"
          />
        ))}
        <path
          d="M0 140 C60 132,88 144,132 119 S218 102,255 112 S322 80,365 91 S431 77,466 61 S532 48,600 22 L600 170 L0 170Z"
          fill="url(#signal-fill)"
        />
        <path
          d="M0 140 C60 132,88 144,132 119 S218 102,255 112 S322 80,365 91 S431 77,466 61 S532 48,600 22"
          fill="none"
          stroke="url(#signal-line)"
          strokeWidth="3"
          strokeLinecap="round"
        />
        <circle cx="466" cy="61" r="5" fill="#34d399" />
        <circle
          cx="466"
          cy="61"
          r="11"
          fill="none"
          stroke="#34d399"
          strokeOpacity=".25"
        />
      </svg>
      <div className="grid grid-cols-3 gap-2 border-t border-white/10 pt-4">
        {[
          ["Signal", "Bullish"],
          ["Confidence", "87%"],
          ["Latency", "< 1s"],
        ].map(([label, value]) => (
          <div key={label}>
            <p className="text-[9px] uppercase tracking-wider text-white/35">
              {label}
            </p>
            <p className="mt-1 text-xs font-semibold text-white/85">{value}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

function safeReturnTo(value: string | null): string {
  if (
    !value ||
    !value.startsWith("/") ||
    value.startsWith("//") ||
    value.includes("\\")
  )
    return "/app/";
  return value;
}

export function LoginPage() {
  const auth = useAuth();
  const [params] = useSearchParams();
  const returnTo = safeReturnTo(params.get("return_to"));
  if (auth.status === "authenticated") return <Navigate to="/" replace />;

  return (
    <main className="auth-surface relative min-h-full overflow-hidden bg-[#080a12] text-white">
      <div className="auth-grid absolute inset-0 opacity-50" />
      <div className="auth-glow auth-glow-one" />
      <div className="auth-glow auth-glow-two" />

      <div className="relative mx-auto grid min-h-screen max-w-[1440px] lg:grid-cols-[1.12fr_0.88fr]">
        <section className="hidden min-h-screen flex-col justify-between px-12 py-10 lg:flex xl:px-20 xl:py-14">
          <div className="flex items-center gap-3">
            <div className="grid h-10 w-10 place-items-center rounded-xl border border-white/15 bg-white/10 shadow-lg shadow-violet-950/30 backdrop-blur">
              <TrendingUp className="h-5 w-5 text-violet-300" />
            </div>
            <div>
              <p className="text-sm font-semibold tracking-tight">
                {branding.productName}
              </p>
              <p className="text-[10px] uppercase tracking-[0.24em] text-white/40">
                Signal intelligence
              </p>
            </div>
          </div>

          <div className="my-auto max-w-2xl py-16">
            <div className="mb-6 inline-flex items-center gap-2 rounded-full border border-violet-300/15 bg-violet-300/5 px-3 py-1.5 text-[11px] font-medium text-violet-200">
              <Sparkles className="h-3.5 w-3.5" /> Built for decisive traders
            </div>
            <h1 className="max-w-xl text-5xl font-semibold leading-[1.04] tracking-[-0.045em] xl:text-6xl">
              Trade with signal,
              <br />
              not noise.
            </h1>
            <p className="mt-6 max-w-xl text-base leading-7 text-white/55">
              Real-time market intelligence, focused alerts, and a clear view of
              what matters—before the opportunity moves on.
            </p>
            <SignalCanvas />
          </div>

          <div className="flex items-center gap-6 text-[11px] text-white/35">
            <span className="flex items-center gap-2">
              <Activity className="h-3.5 w-3.5" /> Real-time monitoring
            </span>
            <span className="flex items-center gap-2">
              <ShieldCheck className="h-3.5 w-3.5" /> Secure by design
            </span>
          </div>
        </section>

        <section className="flex min-h-screen items-center justify-center border-white/10 px-5 py-10 lg:border-l lg:bg-white/[0.015] lg:px-12">
          <div className="auth-card-enter w-full max-w-[440px]">
            <div className="mb-10 flex items-center gap-3 lg:hidden">
              <div className="grid h-10 w-10 place-items-center rounded-xl bg-violet-500/15 text-violet-300">
                <TrendingUp className="h-5 w-5" />
              </div>
              <div>
                <p className="text-sm font-semibold">{branding.productName}</p>
                <p className="text-[10px] uppercase tracking-[0.2em] text-white/40">
                  Signal intelligence
                </p>
              </div>
            </div>

            <div className="rounded-[28px] border border-white/10 bg-white/[0.055] p-6 shadow-2xl shadow-black/40 backdrop-blur-2xl sm:p-8">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <p className="text-xs font-medium text-violet-300">
                    Welcome back
                  </p>
                  <h2 className="mt-2 text-2xl font-semibold tracking-[-0.025em]">
                    Access your dashboard
                  </h2>
                </div>
                <div className="grid h-10 w-10 place-items-center rounded-xl border border-white/10 bg-white/5">
                  <LockKeyhole className="h-4 w-4 text-white/55" />
                </div>
              </div>
              <p className="text-white/48 mt-3 text-sm leading-6">
                Sign in to view your alerts, watchlists, and live market
                signals.
              </p>

              {auth.status === "error" ? (
                <div className="mt-5 rounded-xl border border-amber-300/15 bg-amber-300/5 px-3 py-2.5 text-xs leading-5 text-amber-100/75">
                  {auth.error ?? "Authentication is temporarily unavailable."}
                </div>
              ) : null}

              <div className="mt-7 space-y-3">
                <a
                  href={loginUrl(returnTo, "Google")}
                  className="group flex h-12 w-full items-center justify-center gap-3 rounded-xl bg-white px-4 text-sm font-semibold text-[#141620] shadow-lg shadow-black/15 transition duration-200 hover:-translate-y-0.5 hover:bg-white/95 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-400 focus-visible:ring-offset-2 focus-visible:ring-offset-[#10121b]"
                >
                  <GoogleMark /> Continue with Google
                  <ArrowRight className="ml-auto h-4 w-4 text-black/35 transition-transform group-hover:translate-x-0.5" />
                </a>
                <a
                  href={loginUrl(returnTo)}
                  className="border-white/12 group flex h-12 w-full items-center justify-center gap-3 rounded-xl border bg-white/[0.045] px-4 text-sm font-medium text-white/90 transition duration-200 hover:-translate-y-0.5 hover:border-violet-300/30 hover:bg-violet-300/[0.07] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-400"
                >
                  <Mail className="h-4 w-4 text-white/55" /> Continue with email
                  <ArrowRight className="ml-auto h-4 w-4 text-white/25 transition-transform group-hover:translate-x-0.5" />
                </a>
              </div>

              <div className="mt-4 grid gap-2 sm:grid-cols-2">
                <a
                  href={loginUrl(returnTo, undefined, "signup")}
                  className="group flex h-11 items-center justify-center gap-2 rounded-xl border border-white/12 bg-white/[0.03] px-4 text-sm font-medium text-white/80 transition hover:-translate-y-0.5 hover:border-violet-300/30 hover:bg-violet-300/[0.07]"
                >
                  Create account
                  <ArrowRight className="h-4 w-4 text-white/25 transition-transform group-hover:translate-x-0.5" />
                </a>
                <a
                  href={passwordResetUrl()}
                  className="group flex h-11 items-center justify-center gap-2 rounded-xl border border-white/12 bg-white/[0.03] px-4 text-sm font-medium text-white/80 transition hover:-translate-y-0.5 hover:border-violet-300/30 hover:bg-violet-300/[0.07]"
                >
                  <KeyRound className="h-4 w-4 text-white/45" />
                  Reset password
                </a>
              </div>

              <div className="my-6 flex items-center gap-3 text-[10px] uppercase tracking-[0.18em] text-white/25">
                <div className="h-px flex-1 bg-white/10" /> Production-grade
                security <div className="h-px flex-1 bg-white/10" />
              </div>

              <div className="grid gap-3 sm:grid-cols-2">
                <div className="flex items-start gap-2.5 rounded-xl bg-black/15 p-3">
                  <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-300" />
                  <div>
                    <p className="text-[11px] font-medium text-white/75">
                      Encrypted session
                    </p>
                    <p className="mt-0.5 text-[10px] leading-4 text-white/35">
                      Credentials never reach StockAlert
                    </p>
                  </div>
                </div>
                <div className="flex items-start gap-2.5 rounded-xl bg-black/15 p-3">
                  <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-sky-300" />
                  <div>
                    <p className="text-[11px] font-medium text-white/75">
                      MFA ready
                    </p>
                    <p className="mt-0.5 text-[10px] leading-4 text-white/35">
                      TOTP can be enrolled from the security center after
                      sign-in
                    </p>
                  </div>
                </div>
                <div className="flex items-start gap-2.5 rounded-xl bg-black/15 p-3">
                  <BellRing className="mt-0.5 h-3.5 w-3.5 shrink-0 text-violet-300" />
                  <div>
                    <p className="text-[11px] font-medium text-white/75">
                      Instant alerts
                    </p>
                    <p className="mt-0.5 text-[10px] leading-4 text-white/35">
                      Stay close to every setup
                    </p>
                  </div>
                </div>
              </div>
            </div>

            <p className="mt-6 text-center text-[11px] leading-5 text-white/30">
              By continuing, you agree to the Terms of Service and Privacy
              Policy.
            </p>
          </div>
        </section>
      </div>
    </main>
  );
}
