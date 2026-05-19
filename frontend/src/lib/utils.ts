import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * Combines clsx (conditional classnames) and tailwind-merge (resolves
 * Tailwind conflicts: cn("p-2", "p-4") → "p-4"). The standard helper
 * shadcn/ui components expect; importing as `cn` keeps copy-paste from
 * shadcn examples working without modification.
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
