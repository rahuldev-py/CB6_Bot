# utils/greeks.py — Black-Scholes Option Greeks (Delta, Theta, Gamma, Vega)
#
# Used by Silver Bullet scanner to:
#   - Pick the right strike (Delta 0.65–0.75)
#   - Reject options with runaway theta (theta decay rule: exit if >15 min in FVG)
#   - Back-solve Implied Volatility from live LTP

import numpy as np
from scipy.stats import norm


def calculate_greeks(S: float, K: float, T: float, r: float,
                     sigma: float, option_type: str = 'call') -> dict:
    """
    Black-Scholes Greeks.

    S     : Spot price (Nifty index level)
    K     : Strike price
    T     : Time to expiry in YEARS  (e.g. 7 days → 7/365 = 0.01918)
    r     : Risk-free rate decimal   (0.07 = 7% RBI repo rate)
    sigma : Implied Volatility decimal (0.15 = 15%)
    option_type : 'call' or 'put'

    Returns dict: {delta, theta, gamma, vega}
    delta — how much option moves per 1 pt spot move (target 0.65–0.75)
    theta — time decay per day in rupees (negative — want > −0.5 for weeklies)
    gamma — rate of delta change (higher = more responsive)
    vega  — sensitivity to 1% IV change
    """
    if T <= 0 or sigma <= 0:
        intrinsic = max(S - K, 0) if option_type == 'call' else max(K - S, 0)
        return {'delta': 1.0 if intrinsic > 0 else 0.0,
                'theta': 0.0, 'gamma': 0.0, 'vega': 0.0}

    sqrt_T = np.sqrt(T)
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T

    pdf_d1 = norm.pdf(d1)

    gamma = pdf_d1 / (S * sigma * sqrt_T)
    vega  = S * pdf_d1 * sqrt_T * 0.01   # per 1% move in IV

    if option_type.lower() == 'call':
        delta = norm.cdf(d1)
        theta = (
            -(S * pdf_d1 * sigma) / (2 * sqrt_T)
            - r * K * np.exp(-r * T) * norm.cdf(d2)
        ) / 365
    else:
        delta = norm.cdf(d1) - 1          # negative for puts
        theta = (
            -(S * pdf_d1 * sigma) / (2 * sqrt_T)
            + r * K * np.exp(-r * T) * norm.cdf(-d2)
        ) / 365

    return {
        'delta': round(delta, 4),
        'theta': round(theta, 4),
        'gamma': round(gamma, 6),
        'vega' : round(vega, 4),
    }


def implied_volatility(S: float, K: float, T: float, r: float,
                       market_price: float, option_type: str = 'call',
                       max_iter: int = 100, tol: float = 1e-6) -> float:
    """
    Newton-Raphson IV solver — back-solves sigma from live option LTP.
    Returns IV as decimal (0.18 = 18%). Returns 0.15 if solver fails.
    """
    if T <= 0 or market_price <= 0:
        return 0.15

    sigma = 0.30  # initial guess
    for _ in range(max_iter):
        price = _bs_price(S, K, T, r, sigma, option_type)
        diff  = price - market_price

        d1    = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
        vega  = S * norm.pdf(d1) * np.sqrt(T)
        if abs(vega) < 1e-10:
            break

        sigma_new = sigma - diff / vega
        if abs(sigma_new - sigma) < tol:
            return max(round(sigma_new, 6), 0.01)
        sigma = max(sigma_new, 0.001)

    return round(sigma, 6)


def _bs_price(S: float, K: float, T: float, r: float,
              sigma: float, option_type: str) -> float:
    if T <= 0 or sigma <= 0:
        return max(S - K, 0) if option_type == 'call' else max(K - S, 0)
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    if option_type.lower() == 'call':
        return S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
    return K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def dte_to_years(days_to_expiry: int) -> float:
    """Convert days to expiry → fraction of year for Black-Scholes."""
    return max(days_to_expiry, 0) / 365.0


def theta_ok(theta: float, threshold: float = -2.0) -> bool:
    """
    Silver Bullet theta gate.
    theta is negative (daily decay in Rs). Default rejects > Rs 2/day decay.
    Tighten threshold for shorter DTE or larger lot sizes.
    """
    return theta > threshold
