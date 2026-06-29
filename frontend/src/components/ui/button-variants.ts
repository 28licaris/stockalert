import { cva } from "class-variance-authority";

export const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium ring-offset-bg-base transition-[background,border-color,color,box-shadow,transform] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        default:
          "bg-accent text-accent-fg shadow-[0_0_28px_rgba(46,196,255,0.18)] hover:bg-accent/90 hover:shadow-[0_0_36px_rgba(46,196,255,0.26)]",
        secondary:
          "border border-border-subtle bg-bg-muted text-fg-base hover:border-border hover:bg-bg-elevated",
        ghost: "hover:bg-bg-muted/80 hover:text-fg-base",
        outline:
          "border border-border bg-bg-base/40 hover:border-accent/45 hover:bg-bg-muted/70 hover:text-fg-base",
        destructive: "bg-danger text-fg-inverted hover:bg-danger/90",
      },
      size: {
        default: "h-9 px-4 py-2",
        sm: "h-8 rounded-md px-3 text-xs",
        lg: "h-10 rounded-md px-6",
        icon: "h-9 w-9",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
);
