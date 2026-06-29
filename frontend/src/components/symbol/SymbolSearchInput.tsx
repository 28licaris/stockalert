import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import { Search } from "lucide-react";
import { useInstrumentSearch, type InstrumentMatch } from "@/api/queries";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { cn } from "@/lib/utils";

interface SymbolSearchInputProps {
  /** Current input value (controlled). */
  value: string;
  /** Called on every keystroke. */
  onChange: (next: string) => void;
  /**
   * Called when the user picks a suggestion (click / Enter on highlight)
   * OR submits raw text (Enter with no highlight). `match` is non-null
   * when the user picked a suggestion; null when they submitted raw
   * text — useful for callers that want different behavior in each case.
   */
  onSubmit: (value: string, match: InstrumentMatch | null) => void;
  placeholder?: string;
  /** Limit on the dropdown size. Default 10. */
  limit?: number;
  /**
   * If true the input clears on submit. Useful for "add to a list"
   * forms; not useful for "navigate to /symbol/<sym>" where the input
   * should reflect what was picked.
   */
  clearOnSubmit?: boolean;
  /**
   * Hide the dropdown even when the input has focus + results. Useful
   * for the bulk-paste case where the user is typing a list rather
   * than searching for one symbol.
   */
  suppressDropdown?: boolean;
  /** className applied to the OUTER wrapper (so a parent can stretch the field). */
  className?: string;
  /** className applied to the <input> for theming variants. */
  inputClassName?: string;
  /** Auto-focus on mount. */
  autoFocus?: boolean;
}

/**
 * Symbol search combobox with debounced backend autocomplete.
 *
 * Behavior:
 *   - Debounces `value` by 250 ms before firing /api/v1/instruments/search.
 *   - Dropdown shows up to `limit` suggestions (symbol + description).
 *   - Keyboard: ↓/↑ navigate, Enter selects (or submits raw if no highlight),
 *     Esc closes the dropdown but keeps the input focused.
 *   - Click outside closes the dropdown.
 *
 * a11y:
 *   - role="combobox" on input with aria-expanded + aria-controls.
 *   - role="listbox" on dropdown, role="option" + aria-selected on items.
 *
 * The component does NOT own the symbol normalization (uppercase,
 * trim) — `onSubmit` is called with the input as-typed; callers
 * decide whether to upper-case before persistence.
 */
export function SymbolSearchInput({
  value,
  onChange,
  onSubmit,
  placeholder = "Search ticker or company",
  limit = 10,
  clearOnSubmit = false,
  suppressDropdown = false,
  className,
  inputClassName,
  autoFocus = false,
}: SymbolSearchInputProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [highlight, setHighlight] = useState(-1);
  const wrapperRef = useRef<HTMLDivElement | null>(null);

  // 120ms debounce: fast enough to feel instant on a typical Schwab REST
  // round-trip (~150–250ms cold, ~5ms when the cache hits) while still
  // collapsing keystroke bursts into one call. The backend caches by
  // (query, limit) for 60s so re-typed prefixes are local-only.
  const debounced = useDebouncedValue(value, 120);
  const search = useInstrumentSearch(debounced, limit);
  const results = useMemo<InstrumentMatch[]>(
    () => search.data?.results ?? [],
    [search.data],
  );

  // Close the dropdown when value clears or suppression flips on.
  useEffect(() => {
    if (!value.trim() || suppressDropdown) {
      setIsOpen(false);
      setHighlight(-1);
    } else {
      setIsOpen(true);
    }
  }, [value, suppressDropdown]);

  // Reset highlight when the result set changes.
  useEffect(() => {
    setHighlight(results.length > 0 ? 0 : -1);
  }, [results]);

  // Click-outside to close.
  useEffect(() => {
    if (!isOpen) return;
    const handler = (e: MouseEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [isOpen]);

  const submit = useCallback(
    (raw: string, match: InstrumentMatch | null) => {
      onSubmit(raw, match);
      setIsOpen(false);
      setHighlight(-1);
      if (clearOnSubmit) onChange("");
    },
    [onSubmit, onChange, clearOnSubmit],
  );

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (results.length > 0) {
        setIsOpen(true);
        setHighlight((h) => (h + 1) % results.length);
      }
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      if (results.length > 0) {
        setIsOpen(true);
        setHighlight((h) => (h <= 0 ? results.length - 1 : h - 1));
      }
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      const picked =
        isOpen && highlight >= 0 && highlight < results.length
          ? results[highlight]
          : null;
      submit(picked ? picked.symbol : value, picked);
      return;
    }
    if (e.key === "Escape") {
      setIsOpen(false);
      setHighlight(-1);
    }
  };

  const showDropdown =
    isOpen && !suppressDropdown && value.trim().length > 0 && results.length > 0;
  const listboxId = "symbol-search-listbox";

  return (
    <div ref={wrapperRef} className={cn("relative", className)}>
      <div className="flex h-9 items-center gap-2 rounded-md border border-border bg-bg-base/60 px-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)] transition focus-within:border-accent/70 focus-within:shadow-[0_0_0_1px_rgba(46,196,255,0.12),0_0_28px_rgba(46,196,255,0.1)]">
        <Search className="h-4 w-4 shrink-0 text-fg-subtle" aria-hidden />
        <input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onFocus={() => {
            if (value.trim() && results.length > 0) setIsOpen(true);
          }}
          onKeyDown={onKeyDown}
          placeholder={placeholder}
          autoFocus={autoFocus}
          autoComplete="off"
          spellCheck={false}
          role="combobox"
          aria-expanded={showDropdown}
          aria-controls={listboxId}
          aria-activedescendant={
            showDropdown && highlight >= 0
              ? `symbol-search-option-${highlight}`
              : undefined
          }
          className={cn(
            "h-full flex-1 bg-transparent text-sm uppercase tracking-normal text-fg-base placeholder:normal-case placeholder:tracking-normal focus:outline-none",
            inputClassName,
          )}
        />
        {search.isFetching ? (
          <span
            aria-label="Loading suggestions"
            className="h-2 w-2 animate-pulse rounded-full bg-fg-subtle"
          />
        ) : null}
      </div>

      {showDropdown ? (
        <ul
          id={listboxId}
          role="listbox"
          className="absolute left-0 right-0 top-full z-30 mt-1 max-h-72 overflow-y-auto rounded-md border border-border bg-bg-elevated shadow-2xl shadow-black/40"
        >
          {results.map((match, idx) => {
            const selected = idx === highlight;
            return (
              <li
                key={`${match.symbol}-${idx}`}
                id={`symbol-search-option-${idx}`}
                role="option"
                aria-selected={selected}
                onMouseEnter={() => setHighlight(idx)}
                onMouseDown={(e) => {
                  // mousedown (not click) so the dropdown selection
                  // wins over the input's blur.
                  e.preventDefault();
                  submit(match.symbol, match);
                }}
                className={cn(
                  "flex cursor-pointer items-center justify-between gap-3 px-3 py-2 text-sm",
                  selected ? "bg-accent/10 text-fg-base" : "hover:bg-bg-muted/60",
                )}
              >
                <span className="flex min-w-0 items-center gap-3">
                  <span className="font-mono font-semibold text-fg-base">
                    {match.symbol}
                  </span>
                  {match.description ? (
                    <span className="truncate text-fg-muted">
                      {match.description}
                    </span>
                  ) : null}
                </span>
                <span className="shrink-0 text-[10px] uppercase tracking-wider text-fg-subtle">
                  {match.asset_type || ""}
                </span>
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}
