import { useEffect, useState } from "react";

/**
 * Standard debounce hook. The returned value follows `value` after
 * `delayMs` of no further changes. Used by autocomplete inputs to
 * avoid firing a query on every keystroke.
 */
export function useDebouncedValue<T>(value: T, delayMs: number = 250): T {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(id);
  }, [value, delayMs]);

  return debounced;
}
