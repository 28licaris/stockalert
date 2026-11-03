"""Monte Carlo path samplers — the posterior predictive itself.

Two samplers, selected by `PplParams.vol_model`:

**`simulate_fhs_paths` — GJR-GARCH + Filtered Historical Simulation.** The
production model. Volatility evolves along each path, so a path that draws a
run of negative innovations carries elevated volatility into its next steps.
That feedback is what produces horizon skew and sub-linear variance growth; see
`garch.py` for why nothing without it can.

    eps_h        = sigma_h * z            z drawn from the empirical pool
    log r_h      = mu + tod[slot_h] * eps_h
    sigma^2_{h+1} = omega + (alpha + gamma*1[eps_h<0]) * eps_h^2 + beta * sigma^2_h

**`simulate_paths` — constant-volatility Student-t.** The previous model, kept
as the null hypothesis: it is what `vol_model="ewma_t"` runs, so the PIT harness
can *measure* what GJR-GARCH bought instead of asserting it.

    log r_t = mu + sigma_t * eps_t        eps_t ~ standardized Student-t(nu)

Both share the tail: `P_t = spot * exp(cumsum(log r))`. `mu` is a mean **log**
return estimated from log returns, so it applies directly in log space — there
is no Ito correction to make. Converting an arithmetic drift would need one; we
never hold one.

Deterministic by construction: identical arguments give byte-identical output,
which is what lets a fan be cached and lets a chart survive a page reload
unchanged.
"""
from __future__ import annotations

import hashlib

import numpy as np

from app.services.ppl.estimate import NU_FLOOR
from app.services.ppl.garch import GjrGarchFit


def derive_seed(*parts: object) -> int:
    """Stable 63-bit seed from arbitrary parts.

    `hash()` is salted per process in Python, so it cannot be used for anything
    that has to reproduce across restarts. Digest the string form instead.
    """
    payload = "|".join(str(p) for p in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") >> 1


def simulate_fhs_paths(
    spot: float,
    mu: float,
    fit: GjrGarchFit,
    *,
    tod_factors: np.ndarray,
    start_slot: int,
    bars_per_session: int,
    horizon: int,
    n_paths: int,
    seed: int,
) -> np.ndarray:
    """GJR-GARCH + FHS path simulation. Shape `(n_paths, horizon + 1)`, column 0
    is `spot`.

    Each path carries **its own** variance state. The recursion is driven by the
    *deseasonalized, drift-free* innovation `eps_h`, never by the realized log
    return — feeding `mu` or the time-of-day factor back into the variance
    equation would make the model's persistence depend on its drift setting and
    on what time of day the anchor happened to fall.

    `tod_factors` re-applies the intraday volatility shape the fit removed.
    At daily frequency it is `[1.0]` and this is a no-op.
    """
    if spot <= 0:
        raise ValueError(f"spot must be positive, got {spot}")
    if horizon < 1:
        raise ValueError(f"horizon must be >= 1, got {horizon}")
    if n_paths < 1:
        raise ValueError(f"n_paths must be >= 1, got {n_paths}")
    if bars_per_session < 1:
        raise ValueError(f"bars_per_session must be >= 1, got {bars_per_session}")

    tod = np.asarray(tod_factors, dtype="float64")
    if tod.size < bars_per_session:
        raise ValueError(
            f"tod_factors has {tod.size} entries, need {bars_per_session}"
        )

    rng = np.random.default_rng(seed)
    pool = fit.residual_pool
    omega, alpha, gamma, beta = fit.omega, fit.alpha, fit.gamma, fit.beta

    sigma2 = np.full(n_paths, fit.sigma_next**2, dtype="float64")
    log_r = np.empty((n_paths, horizon), dtype="float64")

    for h in range(horizon):
        # Bootstrap, not a parametric draw: whatever shape the residuals have,
        # the simulation inherits it (garch.py, "Why the innovations are
        # bootstrapped"). Integer indexing rather than rng.choice — same draw,
        # an order of magnitude faster at these sizes.
        z = pool[rng.integers(0, pool.size, size=n_paths)]
        eps = np.sqrt(sigma2) * z

        slot = (start_slot + 1 + h) % bars_per_session
        log_r[:, h] = mu + tod[slot] * eps

        # The leverage term. `gamma > 0` means a down move raises tomorrow's
        # variance more than an up move of equal size, which is the mechanism
        # that grows left skew with horizon.
        eps2 = eps * eps
        sigma2 = omega + (alpha + gamma * (eps < 0.0)) * eps2 + beta * sigma2

    if not np.all(np.isfinite(log_r)):
        # Cannot happen with persistence < 1 and a finite pool, so if it does
        # the model is broken and must say so rather than emit NaN prices.
        raise ValueError(
            "FHS simulation produced non-finite returns — check the GARCH fit"
        )

    paths = spot * np.exp(np.cumsum(log_r, axis=1))
    anchor = np.full((n_paths, 1), spot, dtype="float64")
    return np.hstack([anchor, paths])


def simulate_paths(
    spot: float,
    mu: float,
    sigma_path: np.ndarray,
    *,
    nu: float,
    n_paths: int,
    seed: int,
) -> np.ndarray:
    """Constant-volatility Student-t sampler — the `ewma_t` null model.

    Simulate `n_paths` forward paths over `len(sigma_path)` steps.

    Returns an array of shape `(n_paths, horizon + 1)` whose first column is
    `spot`, so a path can be plotted straight from the anchor.
    """
    if spot <= 0:
        raise ValueError(f"spot must be positive, got {spot}")
    if nu <= NU_FLOOR - 1e-9:
        raise ValueError(f"nu must be >= {NU_FLOOR} for finite variance, got {nu}")
    if n_paths < 1:
        raise ValueError(f"n_paths must be >= 1, got {n_paths}")

    sigma_path = np.asarray(sigma_path, dtype="float64")
    if sigma_path.ndim != 1 or sigma_path.size == 0:
        raise ValueError("sigma_path must be a non-empty 1-D array")
    if not np.all(np.isfinite(sigma_path)) or np.any(sigma_path < 0):
        raise ValueError("sigma_path must be finite and non-negative")

    horizon = sigma_path.size
    rng = np.random.default_rng(seed)

    # Standardize so Var(eps) == 1 and sigma_path is the true per-step sd.
    eps = rng.standard_t(nu, size=(n_paths, horizon)) / np.sqrt(nu / (nu - 2.0))
    log_r = mu + sigma_path[None, :] * eps

    paths = spot * np.exp(np.cumsum(log_r, axis=1))
    anchor = np.full((n_paths, 1), spot, dtype="float64")
    return np.hstack([anchor, paths])


def thin_paths(paths: np.ndarray, n_render: int) -> np.ndarray:
    """Evenly sample `n_render` paths for rendering.

    Deterministic and stratified over the array order (which is itself random),
    so the drawn subset is representative without a second RNG draw. Simulate
    many, draw few — tail quantiles want thousands of paths, a chart wants a
    few hundred lines.
    """
    n = paths.shape[0]
    if n_render >= n:
        return paths
    idx = np.linspace(0, n - 1, n_render).round().astype(int)
    return paths[np.unique(idx)]
