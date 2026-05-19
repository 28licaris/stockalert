import {
  useMutation,
  type UseMutationOptions,
  type UseMutationResult,
} from "@tanstack/react-query";

/**
 * Cost-controlled mutation seam. Today: pass-through to useMutation.
 * Future SaaS: checks the tenant's quota for `operationId` before
 * firing, surfaces remaining-budget headers from the response, blocks
 * with a "you've hit your daily limit" toast on 429.
 *
 * `operationId` is a stable identifier (e.g. "backtest.run",
 * "screener.scan") that maps 1:1 to a column in the backend's per-
 * tenant quota table.
 */

export interface QuotaInfo {
  remaining: number | null;
  limit: number | null;
  resetAt: string | null;
}

// useMutation returns a discriminated union (idle/pending/error/success
// each with different non-nullable shapes), so we attach `quota` via
// intersection rather than `extends interface`.
export type UseQuotaMutationResult<TData, TError, TVariables, TContext> =
  UseMutationResult<TData, TError, TVariables, TContext> & {
    quota: QuotaInfo;
  };

export function useQuotaMutation<TData, TError, TVariables, TContext>(
  _operationId: string,
  options: UseMutationOptions<TData, TError, TVariables, TContext>,
): UseQuotaMutationResult<TData, TError, TVariables, TContext> {
  const mutation = useMutation<TData, TError, TVariables, TContext>(options);

  const quota: QuotaInfo = {
    remaining: null,
    limit: null,
    resetAt: null,
  };

  return Object.assign(mutation, { quota });
}
