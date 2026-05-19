/**
 * Single source of truth for product identity. Components must NEVER
 * hardcode "StockAlert" or the logo path — read from here.
 *
 * Future SaaS / white-label: this file becomes per-tenant lookup
 * (resolved server-side from the tenant config) — every component that
 * already reads from here automatically picks up the override.
 */

export const branding = {
  productName: "StockAlert",
  productTagline: "Developer cockpit",
  // Single-character glyph used in the sidebar collapse state. Replace
  // with a logo SVG when we have one.
  shortMark: "S",
  // Marketing site URL (will be set when SaaS lands; harmless empty).
  marketingUrl: "",
  // Support / docs URLs (point at GitHub repo for now).
  docsUrl: "https://github.com/", // TODO: replace with real repo URL
  supportEmail: "",
} as const;

export type Branding = typeof branding;
