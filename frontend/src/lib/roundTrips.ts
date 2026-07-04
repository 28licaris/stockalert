/**
 * Round-trip pairing — strategy-agnostic.
 *
 * The engine reports individual fills (legs); this pairs each closing leg
 * with the opening leg(s) since the symbol was last flat, producing
 * entry -> exit rows for display. Works for any strategy (long or short,
 * partial closes, multi-symbol) because it only relies on the generic
 * Trade shape every backtest/paper run emits.
 */

export interface TradeLeg {
  symbol: string;
  side: string;
  quantity: number;
  price: number;
  timestamp: string;
  realized_pnl: number;
  holding_days: number;
  is_closing: boolean;
  note?: string;
}

export interface RoundTrip {
  symbol: string;
  direction: "long" | "short";
  entryDate: string;
  entryPrice: number;
  exitDate: string;
  exitPrice: number;
  quantity: number;
  pnl: number;
  holdingDays: number;
  note?: string;
}

interface OpenLot {
  qty: number;
  cost: number;
  firstDate: string;
  dir: "long" | "short";
}

export function pairRoundTrips(trades: TradeLeg[]): RoundTrip[] {
  const open = new Map<string, OpenLot>();
  const out: RoundTrip[] = [];
  for (const t of trades) {
    if (!t.is_closing) {
      const dir: "long" | "short" = t.side === "buy" ? "long" : "short";
      const cur = open.get(t.symbol);
      if (!cur || cur.qty <= 0) {
        open.set(t.symbol, { qty: t.quantity, cost: t.price * t.quantity, firstDate: t.timestamp, dir });
      } else {
        cur.qty += t.quantity;
        cur.cost += t.price * t.quantity;
      }
    } else {
      const cur = open.get(t.symbol);
      const avg = cur && cur.qty > 0 ? cur.cost / cur.qty : NaN;
      out.push({
        symbol: t.symbol,
        direction: cur?.dir ?? (t.side === "sell" ? "long" : "short"),
        entryDate: cur?.firstDate ?? "",
        entryPrice: avg,
        exitDate: t.timestamp,
        exitPrice: t.price,
        quantity: t.quantity,
        pnl: t.realized_pnl,
        holdingDays: t.holding_days,
        note: t.note,
      });
      if (cur) {
        const q = Math.min(cur.qty, t.quantity);
        cur.cost -= (Number.isNaN(avg) ? 0 : avg) * q;
        cur.qty -= q;
        if (cur.qty <= 1e-9) open.delete(t.symbol);
      }
    }
  }
  return out;
}
