import { useMemo, useState } from "react";
import {
  Activity,
  ArrowDownRight,
  ArrowUpRight,
  Filter,
  RefreshCw,
  Search,
  Sigma,
} from "lucide-react";
import {
  useLatestOptionContracts,
  useLatestOptionGex,
  type GammaAggregationLevel,
  type GammaExposureSnapshot,
  type OptionContractSnapshot,
  type PutCall,
} from "@/api/queries";
import { ApiErrorAlert } from "@/components/ApiErrorAlert";
import { Button } from "@/components/ui/button";
import { fmtAgo, fmtDate, fmtInt, fmtPrice, fmtVol } from "@/lib/fmt";
import { cn } from "@/lib/utils";

type SideFilter = "ALL" | PutCall;

const GEX_LEVELS: { value: GammaAggregationLevel; label: string }[] = [
  { value: "total", label: "Total" },
  { value: "strike", label: "Strike" },
  { value: "expiry", label: "Expiry" },
  { value: "strike_expiry", label: "Strike + Expiry" },
];

const CONTRACT_LIMITS = [50, 100, 250, 500] as const;
const GEX_LIMITS = [25, 50, 100, 250] as const;

export function OptionsPage() {
  const [draftSymbol, setDraftSymbol] = useState("AAPL");
  const [symbol, setSymbol] = useState("AAPL");
  const [side, setSide] = useState<SideFilter>("ALL");
  const [expirationDate, setExpirationDate] = useState("");
  const [contractLimit, setContractLimit] = useState<number>(100);
  const [gexLevel, setGexLevel] = useState<GammaAggregationLevel>("strike");
  const [gexLimit, setGexLimit] = useState<number>(50);

  const putCall = side === "ALL" ? undefined : side;
  const contracts = useLatestOptionContracts({
    symbol,
    expirationDate: expirationDate || undefined,
    putCall,
    limit: contractLimit,
  });
  const gex = useLatestOptionGex({
    symbol,
    aggregationLevel: gexLevel,
    limit: gexLimit,
  });
  const totalGex = useLatestOptionGex({
    symbol,
    aggregationLevel: "total",
    limit: 1,
  });

  const latestSnapshotTs = useMemo(
    () => latestTs(contracts.data?.contracts, gex.data?.rows),
    [contracts.data?.contracts, gex.data?.rows],
  );
  const summary = useMemo(
    () => buildSummary(totalGex.data?.rows[0], contracts.data?.contracts ?? []),
    [contracts.data?.contracts, totalGex.data?.rows],
  );

  const refreshAll = () => {
    contracts.refetch();
    gex.refetch();
    totalGex.refetch();
  };

  const applySymbol = () => {
    const next = draftSymbol.trim().toUpperCase();
    if (next) setSymbol(next);
  };

  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-5 p-4 sm:p-6">
      <header className="flex flex-col gap-4 border-b border-border pb-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-fg-base">Options</h1>
          <p className="mt-1 max-w-3xl text-sm text-fg-muted">
            Latest options-chain snapshots for GEX, positioning, and
            contract-level opportunity scans.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3 text-xs text-fg-subtle">
          <span>
            {latestSnapshotTs
              ? `Snapshot ${fmtAgo(latestSnapshotTs)}`
              : "No hot snapshot loaded"}
          </span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={refreshAll}
            disabled={
              contracts.isFetching || gex.isFetching || totalGex.isFetching
            }
          >
            <RefreshCw
              className={cn(
                "h-3.5 w-3.5",
                (contracts.isFetching ||
                  gex.isFetching ||
                  totalGex.isFetching) &&
                  "animate-spin",
              )}
            />
            Refresh
          </Button>
        </div>
      </header>

      <section className="grid gap-3 rounded-md border border-border bg-bg-subtle p-3 lg:grid-cols-[minmax(220px,1fr)_auto_auto_auto]">
        <form
          className="flex min-w-0 items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            applySymbol();
          }}
        >
          <label className="sr-only" htmlFor="options-symbol">
            Underlying symbol
          </label>
          <div className="flex min-w-0 flex-1 items-center gap-2 rounded-md border border-border bg-bg-base px-3">
            <Search className="h-4 w-4 shrink-0 text-fg-subtle" />
            <input
              id="options-symbol"
              value={draftSymbol}
              onChange={(event) => setDraftSymbol(event.target.value)}
              className="h-9 min-w-0 flex-1 bg-transparent font-mono text-sm uppercase text-fg-base outline-none placeholder:text-fg-subtle"
              placeholder="AAPL"
              spellCheck={false}
            />
          </div>
          <Button type="submit" size="sm">
            Load
          </Button>
        </form>

        <SegmentedControl
          label="Side"
          value={side}
          items={[
            { value: "ALL", label: "All" },
            { value: "CALL", label: "Calls" },
            { value: "PUT", label: "Puts" },
          ]}
          onChange={(value) => setSide(value as SideFilter)}
        />

        <label className="flex items-center gap-2 text-xs text-fg-muted">
          Expiry
          <input
            type="date"
            value={expirationDate}
            onChange={(event) => setExpirationDate(event.target.value)}
            className="h-9 rounded-md border border-border bg-bg-base px-2 text-sm text-fg-base outline-none"
          />
        </label>

        <label className="flex items-center gap-2 text-xs text-fg-muted">
          Contracts
          <select
            value={contractLimit}
            onChange={(event) => setContractLimit(Number(event.target.value))}
            className="h-9 rounded-md border border-border bg-bg-base px-2 text-sm text-fg-base outline-none"
          >
            {CONTRACT_LIMITS.map((limit) => (
              <option key={limit} value={limit}>
                {limit}
              </option>
            ))}
          </select>
        </label>
      </section>

      {contracts.error ? <ApiErrorAlert error={contracts.error} /> : null}
      {gex.error ? <ApiErrorAlert error={gex.error} /> : null}
      {totalGex.error ? <ApiErrorAlert error={totalGex.error} /> : null}

      <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <MetricPanel
          title="Net GEX"
          value={fmtSignedCompact(summary.netGammaExposure)}
          tone={
            summary.netGammaExposure == null || summary.netGammaExposure >= 0
              ? "up"
              : "down"
          }
          detail={
            summary.netGammaExposure == null
              ? "Waiting for total GEX"
              : summary.netGammaExposure >= 0
                ? "Long gamma"
                : "Short gamma"
          }
        />
        <MetricPanel
          title="Call GEX"
          value={fmtSignedCompact(summary.callGammaExposure)}
          tone="up"
          detail={`${fmtInt(summary.callOpenInterest)} call OI`}
        />
        <MetricPanel
          title="Put GEX"
          value={fmtSignedCompact(summary.putGammaExposure)}
          tone="down"
          detail={`${fmtInt(summary.putOpenInterest)} put OI`}
        />
        <MetricPanel
          title="Underlying"
          value={fmtPrice(summary.underlyingPrice)}
          tone="neutral"
          detail={`${fmtInt(summary.contractCount)} contracts in view`}
        />
      </section>

      <section className="grid min-h-0 gap-5 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.5fr)]">
        <div className="min-w-0 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <Sigma className="h-4 w-4 text-accent" />
              <h2 className="text-sm font-semibold uppercase tracking-wider text-fg-subtle">
                Gamma Exposure
              </h2>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <SegmentedControl
                label="GEX level"
                value={gexLevel}
                items={GEX_LEVELS}
                onChange={(value) =>
                  setGexLevel(value as GammaAggregationLevel)
                }
              />
              <select
                value={gexLimit}
                onChange={(event) => setGexLimit(Number(event.target.value))}
                className="h-8 rounded-md border border-border bg-bg-base px-2 text-xs text-fg-base outline-none"
                aria-label="GEX row limit"
              >
                {GEX_LIMITS.map((limit) => (
                  <option key={limit} value={limit}>
                    {limit}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <GexTable rows={gex.data?.rows ?? []} loading={gex.isLoading} />
        </div>

        <div className="min-w-0 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <Activity className="h-4 w-4 text-accent" />
              <h2 className="text-sm font-semibold uppercase tracking-wider text-fg-subtle">
                Contracts
              </h2>
            </div>
            <div className="flex items-center gap-2 text-xs text-fg-subtle">
              <Filter className="h-3.5 w-3.5" />
              <span>{symbol}</span>
              <span>{side === "ALL" ? "All sides" : side}</span>
              {expirationDate ? <span>{expirationDate}</span> : null}
            </div>
          </div>
          <ContractsTable
            contracts={contracts.data?.contracts ?? []}
            loading={contracts.isLoading}
          />
        </div>
      </section>
    </div>
  );
}

function SegmentedControl({
  label,
  value,
  items,
  onChange,
}: {
  label: string;
  value: string;
  items: ReadonlyArray<{ value: string; label: string }>;
  onChange: (value: string) => void;
}) {
  return (
    <div
      role="group"
      aria-label={label}
      className="inline-flex rounded-md border border-border bg-bg-base p-0.5"
    >
      {items.map((item) => (
        <button
          key={item.value}
          type="button"
          onClick={() => onChange(item.value)}
          className={cn(
            "rounded-sm px-2.5 py-1.5 text-xs font-medium transition-colors",
            value === item.value
              ? "bg-accent text-accent-fg"
              : "text-fg-muted hover:bg-bg-muted hover:text-fg-base",
          )}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}

function MetricPanel({
  title,
  value,
  detail,
  tone,
}: {
  title: string;
  value: string;
  detail: string;
  tone: "up" | "down" | "neutral";
}) {
  const Icon = tone === "down" ? ArrowDownRight : ArrowUpRight;
  return (
    <div className="rounded-md border border-border bg-bg-subtle p-4">
      <div className="flex items-center justify-between gap-3">
        <div className="text-xs uppercase tracking-wider text-fg-subtle">
          {title}
        </div>
        <Icon
          className={cn(
            "h-4 w-4",
            tone === "up" && "text-up",
            tone === "down" && "text-down",
            tone === "neutral" && "text-fg-subtle",
          )}
        />
      </div>
      <div
        className={cn(
          "mt-3 truncate font-mono text-2xl font-semibold",
          tone === "up" && "text-up",
          tone === "down" && "text-down",
          tone === "neutral" && "text-fg-base",
        )}
      >
        {value}
      </div>
      <div className="mt-1 truncate text-xs text-fg-muted">{detail}</div>
    </div>
  );
}

function GexTable({
  rows,
  loading,
}: {
  rows: ReadonlyArray<GammaExposureSnapshot>;
  loading: boolean;
}) {
  const maxAbs = Math.max(
    1,
    ...rows.map((row) =>
      Math.abs(row.net_gamma_exposure ?? row.gamma_exposure),
    ),
  );
  return (
    <div className="overflow-hidden rounded-md border border-border bg-bg-subtle">
      <div className="max-h-[34rem] overflow-auto">
        <table className="w-full min-w-[620px] text-sm">
          <thead className="sticky top-0 bg-bg-muted text-xs uppercase tracking-wider text-fg-subtle">
            <tr>
              <th className="px-3 py-2 text-left font-medium">Level</th>
              <th className="px-3 py-2 text-right font-medium">Net GEX</th>
              <th className="px-3 py-2 text-right font-medium">Call</th>
              <th className="px-3 py-2 text-right font-medium">Put</th>
              <th className="px-3 py-2 text-right font-medium">OI</th>
              <th className="px-3 py-2 text-right font-medium">Contracts</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border-subtle">
            {loading ? (
              <EmptyRow colSpan={6} text="Loading gamma exposure…" />
            ) : rows.length === 0 ? (
              <EmptyRow
                colSpan={6}
                text="No latest GEX rows for this symbol."
              />
            ) : (
              rows.map((row) => {
                const net = row.net_gamma_exposure ?? row.gamma_exposure;
                return (
                  <tr key={row.level_key} className="hover:bg-bg-muted/40">
                    <td className="px-3 py-2">
                      <div className="font-mono text-xs text-fg-base">
                        {gexLabel(row)}
                      </div>
                      <div className="mt-1 h-1.5 overflow-hidden rounded bg-bg-muted">
                        <div
                          className={cn(
                            "h-full",
                            net >= 0 ? "bg-up" : "bg-down",
                          )}
                          style={{
                            width: `${Math.max(4, (Math.abs(net) / maxAbs) * 100)}%`,
                          }}
                        />
                      </div>
                    </td>
                    <td
                      className={cn(
                        "px-3 py-2 text-right font-mono",
                        net >= 0 ? "text-up" : "text-down",
                      )}
                    >
                      {fmtSignedCompact(net)}
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-up">
                      {fmtSignedCompact(row.call_gamma_exposure)}
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-down">
                      {fmtSignedCompact(row.put_gamma_exposure)}
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-fg-muted">
                      {fmtInt(row.open_interest)}
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-fg-muted">
                      {fmtInt(row.contract_count)}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ContractsTable({
  contracts,
  loading,
}: {
  contracts: ReadonlyArray<OptionContractSnapshot>;
  loading: boolean;
}) {
  return (
    <div className="overflow-hidden rounded-md border border-border bg-bg-subtle">
      <div className="max-h-[34rem] overflow-auto">
        <table className="w-full min-w-[900px] text-sm">
          <thead className="sticky top-0 bg-bg-muted text-xs uppercase tracking-wider text-fg-subtle">
            <tr>
              <th className="px-3 py-2 text-left font-medium">Contract</th>
              <th className="px-3 py-2 text-right font-medium">Mark</th>
              <th className="px-3 py-2 text-right font-medium">Bid / Ask</th>
              <th className="px-3 py-2 text-right font-medium">IV</th>
              <th className="px-3 py-2 text-right font-medium">Delta</th>
              <th className="px-3 py-2 text-right font-medium">Gamma</th>
              <th className="px-3 py-2 text-right font-medium">Vol</th>
              <th className="px-3 py-2 text-right font-medium">OI</th>
              <th className="px-3 py-2 text-right font-medium">Quote</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border-subtle">
            {loading ? (
              <EmptyRow colSpan={9} text="Loading contracts…" />
            ) : contracts.length === 0 ? (
              <EmptyRow
                colSpan={9}
                text="No latest contracts for this symbol and filter."
              />
            ) : (
              contracts.map((contract) => (
                <tr
                  key={`${contract.option_symbol}-${contract.snapshot_ts}`}
                  className="hover:bg-bg-muted/40"
                >
                  <td className="px-3 py-2">
                    <div className="flex items-center gap-2">
                      <span
                        className={cn(
                          "inline-flex h-5 w-9 items-center justify-center rounded-sm text-[10px] font-semibold",
                          contract.put_call === "CALL"
                            ? "bg-up/15 text-up"
                            : "bg-down/15 text-down",
                        )}
                      >
                        {contract.put_call === "CALL" ? "C" : "P"}
                      </span>
                      <div className="min-w-0">
                        <div className="truncate font-mono text-xs text-fg-base">
                          {fmtDate(contract.expiration_date)}{" "}
                          {fmtPrice(contract.strike)}
                        </div>
                        <div className="truncate font-mono text-[11px] text-fg-subtle">
                          {contract.option_symbol}
                        </div>
                      </div>
                    </div>
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-fg-base">
                    {fmtPrice(contract.mark ?? contract.last)}
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-fg-muted">
                    {fmtPrice(contract.bid)} / {fmtPrice(contract.ask)}
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-fg-muted">
                    {fmtPctFromDecimal(contract.volatility)}
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-fg-muted">
                    {fmtGreek(contract.delta)}
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-fg-muted">
                    {fmtGreek(contract.gamma)}
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-fg-muted">
                    {fmtVol(contract.volume)}
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-fg-muted">
                    {fmtInt(contract.open_interest)}
                  </td>
                  <td className="px-3 py-2 text-right text-xs text-fg-muted">
                    {fmtAgo(contract.quote_time ?? contract.snapshot_ts)}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function EmptyRow({ colSpan, text }: { colSpan: number; text: string }) {
  return (
    <tr>
      <td
        colSpan={colSpan}
        className="px-3 py-8 text-center text-sm text-fg-subtle"
      >
        {text}
      </td>
    </tr>
  );
}

function latestTs(
  contracts: ReadonlyArray<OptionContractSnapshot> | undefined,
  rows: ReadonlyArray<GammaExposureSnapshot> | undefined,
): string | null {
  const timestamps = [
    ...(contracts ?? []).map((contract) => contract.snapshot_ts),
    ...(rows ?? []).map((row) => row.snapshot_ts),
  ];
  if (timestamps.length === 0) return null;
  return timestamps.reduce((latest, ts) =>
    new Date(ts).getTime() > new Date(latest).getTime() ? ts : latest,
  );
}

function buildSummary(
  total: GammaExposureSnapshot | undefined,
  contracts: ReadonlyArray<OptionContractSnapshot>,
) {
  const callContracts = contracts.filter(
    (contract) => contract.put_call === "CALL",
  );
  const putContracts = contracts.filter(
    (contract) => contract.put_call === "PUT",
  );
  const underlyingPrice =
    total?.underlying_price ??
    contracts.find((contract) => contract.underlying_price != null)
      ?.underlying_price ??
    null;
  return {
    netGammaExposure:
      total?.net_gamma_exposure ?? total?.gamma_exposure ?? null,
    callGammaExposure: total?.call_gamma_exposure ?? null,
    putGammaExposure: total?.put_gamma_exposure ?? null,
    callOpenInterest: sumOpenInterest(callContracts),
    putOpenInterest: sumOpenInterest(putContracts),
    underlyingPrice,
    contractCount: total?.contract_count ?? contracts.length,
  };
}

function sumOpenInterest(
  contracts: ReadonlyArray<OptionContractSnapshot>,
): number {
  return contracts.reduce(
    (sum, contract) => sum + (contract.open_interest ?? 0),
    0,
  );
}

function gexLabel(row: GammaExposureSnapshot): string {
  if (row.aggregation_level === "total") return "Total";
  if (row.aggregation_level === "expiry") {
    return row.expiration_date ? fmtDate(row.expiration_date) : "Expiry";
  }
  if (row.aggregation_level === "strike") {
    return row.strike == null ? "Strike" : fmtPrice(row.strike);
  }
  return `${row.expiration_date ? fmtDate(row.expiration_date) : "Expiry"} ${row.strike == null ? "" : fmtPrice(row.strike)}`;
}

function fmtSignedCompact(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  const sign = value > 0 ? "+" : "";
  const abs = Math.abs(value);
  if (abs >= 1e9) return `${sign}${(value / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${sign}${(value / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${sign}${(value / 1e3).toFixed(1)}K`;
  return `${sign}${value.toFixed(0)}`;
}

function fmtGreek(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return value.toFixed(4);
}

function fmtPctFromDecimal(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  const normalized = Math.abs(value) > 1 ? value : value * 100;
  return `${normalized.toFixed(1)}%`;
}
