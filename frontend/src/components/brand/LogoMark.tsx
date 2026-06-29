import { cn } from "@/lib/utils";

interface LogoMarkProps {
  className?: string;
  wordmark?: boolean;
}

export function LogoMark({ className, wordmark = false }: LogoMarkProps) {
  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      <span className="relative grid h-8 w-8 shrink-0 place-items-center overflow-hidden rounded-md border border-accent/40 bg-bg-base shadow-[0_0_28px_rgba(46,196,255,0.18)]">
        <span className="absolute inset-0 bg-[radial-gradient(circle_at_50%_42%,rgba(46,196,255,0.25),transparent_56%)]" />
        <svg
          viewBox="0 0 32 32"
          aria-hidden
          className="relative h-6 w-6 text-accent"
          fill="none"
        >
          <circle cx="16" cy="16" r="3.3" fill="currentColor" />
          <path
            d="M9.5 18.5a7.25 7.25 0 0 1 10-9.1"
            stroke="currentColor"
            strokeLinecap="round"
            strokeWidth="2"
          />
          <path
            d="M22.9 12.5a7.25 7.25 0 0 1-10.1 10"
            stroke="currentColor"
            strokeLinecap="round"
            strokeWidth="2"
          />
          <path
            d="M8 24 24 8"
            stroke="white"
            strokeLinecap="round"
            strokeOpacity="0.82"
            strokeWidth="1.5"
          />
        </svg>
      </span>
      {wordmark ? (
        <span className="min-w-0 leading-none">
          <span className="font-display text-sm font-semibold tracking-normal text-fg-base">
            Stock<span className="text-accent">Alert</span>
          </span>
        </span>
      ) : null}
    </span>
  );
}
