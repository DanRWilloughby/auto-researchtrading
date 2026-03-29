#!/usr/bin/env python3
"""
Forte Investment Fund — Portfolio Optimizer for Paterson NJ Deal Sourcing

Given a fund raise amount, computes the optimal mix of properties to acquire
from the ranked deals pipeline. Uses greedy selection with diversification
constraints followed by swap-based improvement.

Usage:
  python portfolio_optimizer.py                    # Default $5M budget
  python portfolio_optimizer.py --budget 2000000   # $2M budget
  python portfolio_optimizer.py --budget 10000000  # $10M budget
  python portfolio_optimizer.py --oz-only          # Only Opportunity Zone properties
  python portfolio_optimizer.py --min-props 5 --max-props 15
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DATA_PATH = Path(__file__).parent / "data" / "paterson_ranked_deals.csv"

DEFAULT_BUDGET = 5_000_000
MIN_PROPERTIES = 3
MAX_PROPERTIES = 20
MAX_ZIP_PCT = 0.30        # No more than 30% of budget in a single ZIP
MAX_CLASS_PCT = 0.40      # No more than 40% of budget in a single property class
MIN_EQUITY_MULTIPLE = 1.0
PREFER_DISTRESS_SCORE = 2

# Scoring weights for blended return metric
W_EQUITY_MULTIPLE = 0.40
W_CAP_RATE = 0.30
W_CASH_YIELD = 0.30

# Bonus multipliers
BONUS_OZ = 1.08           # 8% bonus for Opportunity Zone
BONUS_SHERIFF = 1.10      # 10% bonus for sheriff sale
BONUS_ABSENTEE = 1.04     # 4% bonus for absentee/out-of-state
BONUS_DISTRESS = 1.05     # 5% bonus for distress_score >= PREFER_DISTRESS_SCORE

# Swap improvement
MAX_SWAP_ROUNDS = 3


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def compute_selection_score(row: pd.Series) -> float:
    """
    Compute a blended selection score for a single property.
    Combines equity multiple, cap rate, and cash-on-cash yield with bonuses.
    """
    # Normalize equity multiple: clip to [1, 10] and scale to [0, 1]
    eq_norm = min(max((row["equity_multiple"] - 1.0) / 9.0, 0.0), 1.0)

    # Normalize cap rate: clip to [0, 0.30] and scale to [0, 1]
    cap_norm = min(max(row["cap_rate_current"] / 0.30, 0.0), 1.0)

    # Cash yield = NOI stabilized / total cost
    total_cost = row["total_cost"]
    if total_cost > 0:
        cash_yield = row["noi_stabilized"] / total_cost
        cy_norm = min(max(cash_yield / 0.20, 0.0), 1.0)
    else:
        cy_norm = 0.0

    base = (W_EQUITY_MULTIPLE * eq_norm +
            W_CAP_RATE * cap_norm +
            W_CASH_YIELD * cy_norm)

    # Apply bonuses
    if row.get("in_opportunity_zone", False):
        base *= BONUS_OZ
    if row.get("in_sheriff_sale", False):
        base *= BONUS_SHERIFF
    if row.get("absentee_status") in ("out_of_state", "absentee_nj"):
        base *= BONUS_ABSENTEE
    if row.get("distress_score", 0) >= PREFER_DISTRESS_SCORE:
        base *= BONUS_DISTRESS

    return base


# ---------------------------------------------------------------------------
# Constraint Checking
# ---------------------------------------------------------------------------
def check_constraints(portfolio_indices: list, df: pd.DataFrame, budget: float,
                      min_props: int, max_props: int) -> bool:
    """Return True if the portfolio satisfies all hard constraints."""
    if len(portfolio_indices) < min_props or len(portfolio_indices) > max_props:
        return False

    sub = df.loc[portfolio_indices]
    total = sub["total_cost"].sum()
    if total > budget:
        return False

    # ZIP diversification
    zip_spend = sub.groupby("zip")["total_cost"].sum()
    if (zip_spend > budget * MAX_ZIP_PCT).any():
        return False

    # Property class diversification
    class_spend = sub.groupby("prop_class")["total_cost"].sum()
    if (class_spend > budget * MAX_CLASS_PCT).any():
        return False

    return True


def can_add(candidate_idx: int, portfolio_indices: list, df: pd.DataFrame,
            budget: float, max_props: int) -> bool:
    """Check whether adding candidate_idx to portfolio still satisfies constraints."""
    if len(portfolio_indices) >= max_props:
        return False

    cand = df.loc[candidate_idx]
    sub = df.loc[portfolio_indices] if portfolio_indices else pd.DataFrame()

    new_total = (sub["total_cost"].sum() if len(sub) else 0) + cand["total_cost"]
    if new_total > budget:
        return False

    # ZIP check
    zip_code = cand["zip"]
    zip_existing = sub[sub["zip"] == zip_code]["total_cost"].sum() if len(sub) else 0
    if (zip_existing + cand["total_cost"]) > budget * MAX_ZIP_PCT:
        return False

    # Class check
    prop_class = cand["prop_class"]
    class_existing = sub[sub["prop_class"] == prop_class]["total_cost"].sum() if len(sub) else 0
    if (class_existing + cand["total_cost"]) > budget * MAX_CLASS_PCT:
        return False

    return True


# ---------------------------------------------------------------------------
# Greedy Selection
# ---------------------------------------------------------------------------
def greedy_select(df: pd.DataFrame, budget: float,
                  min_props: int, max_props: int) -> list:
    """
    Greedy selection: iterate through properties sorted by selection_score desc,
    adding each if it doesn't violate constraints.
    """
    sorted_indices = df.sort_values("selection_score", ascending=False).index.tolist()
    portfolio = []

    for idx in sorted_indices:
        if len(portfolio) >= max_props:
            break
        if can_add(idx, portfolio, df, budget, max_props):
            portfolio.append(idx)

    return portfolio


# ---------------------------------------------------------------------------
# Swap Improvement
# ---------------------------------------------------------------------------
def swap_improve(portfolio: list, df: pd.DataFrame, budget: float,
                 min_props: int, max_props: int) -> list:
    """
    Try to improve the portfolio by swapping each property with a better
    non-portfolio property that still satisfies constraints.
    """
    portfolio_set = set(portfolio)
    candidates = [i for i in df.index if i not in portfolio_set]
    # Sort candidates by score desc for efficiency
    candidates.sort(key=lambda i: df.loc[i, "selection_score"], reverse=True)

    improved = True
    rounds = 0

    while improved and rounds < MAX_SWAP_ROUNDS:
        improved = False
        rounds += 1

        for port_pos, port_idx in enumerate(list(portfolio)):
            port_score = df.loc[port_idx, "selection_score"]

            for cand_idx in candidates:
                if cand_idx in portfolio_set:
                    continue
                cand_score = df.loc[cand_idx, "selection_score"]
                # Only try if candidate scores higher
                if cand_score <= port_score:
                    break  # candidates sorted desc, no better ones ahead

                # Try swap
                trial = [x for x in portfolio if x != port_idx] + [cand_idx]
                if check_constraints(trial, df, budget, min_props, max_props):
                    # Accept swap
                    portfolio = trial
                    portfolio_set.discard(port_idx)
                    portfolio_set.add(cand_idx)
                    candidates = [i for i in candidates if i != cand_idx]
                    candidates.append(port_idx)
                    improved = True
                    break  # restart inner loop with updated portfolio

        # Also try adding more properties if under max
        if len(portfolio) < max_props:
            for cand_idx in candidates:
                if cand_idx in portfolio_set:
                    continue
                if can_add(cand_idx, portfolio, df, budget, max_props):
                    portfolio.append(cand_idx)
                    portfolio_set.add(cand_idx)
                    candidates = [i for i in candidates if i != cand_idx]
                    improved = True
                    break

    return portfolio


# ---------------------------------------------------------------------------
# Naive Portfolio (comparison baseline)
# ---------------------------------------------------------------------------
def naive_select(df: pd.DataFrame, budget: float, max_props: int) -> list:
    """
    Naive selection: top N properties by composite_score that fit in budget,
    with NO diversification constraints.
    """
    sorted_df = df.sort_values("composite_score", ascending=False)
    portfolio = []
    running_cost = 0

    for idx, row in sorted_df.iterrows():
        if len(portfolio) >= max_props:
            break
        if running_cost + row["total_cost"] <= budget:
            portfolio.append(idx)
            running_cost += row["total_cost"]

    return portfolio


# ---------------------------------------------------------------------------
# Portfolio Metrics
# ---------------------------------------------------------------------------
def compute_metrics(portfolio: list, df: pd.DataFrame) -> dict:
    """Compute summary metrics for a portfolio."""
    sub = df.loc[portfolio]
    total_cost = sub["total_cost"].sum()
    total_market_value = sub["est_market_value"].sum()
    total_reno = sub["est_reno_cost"].sum()
    total_noi_current = sub["noi_current"].sum()
    total_noi_stabilized = sub["noi_stabilized"].sum()
    total_rent = sub["gross_annual_rent"].sum()
    total_units = sub["est_units"].sum()
    total_exit = sub["exit_value"].sum()

    blended_cap = total_noi_current / total_market_value if total_market_value else 0
    blended_cap_stabilized = total_noi_stabilized / total_market_value if total_market_value else 0
    blended_equity_multiple = total_exit / total_cost if total_cost else 0
    cash_yield = total_noi_stabilized / total_cost if total_cost else 0
    avg_composite = sub["composite_score"].mean()
    avg_selection = sub["selection_score"].mean()
    total_tax = sub["annual_tax"].sum()

    return {
        "num_properties": len(portfolio),
        "total_units": int(total_units),
        "total_cost": total_cost,
        "total_market_value": total_market_value,
        "total_reno": total_reno,
        "total_noi_current": total_noi_current,
        "total_noi_stabilized": total_noi_stabilized,
        "total_gross_rent": total_rent,
        "total_exit_value": total_exit,
        "total_annual_tax": total_tax,
        "blended_cap_rate": blended_cap,
        "blended_cap_rate_stabilized": blended_cap_stabilized,
        "blended_equity_multiple": blended_equity_multiple,
        "cash_on_cash_yield": cash_yield,
        "avg_composite_score": avg_composite,
        "avg_selection_score": avg_selection,
    }


# ---------------------------------------------------------------------------
# Sensitivity Analysis
# ---------------------------------------------------------------------------
def sensitivity_analysis(portfolio: list, df: pd.DataFrame) -> dict:
    """
    Compute portfolio metrics under stress scenarios:
    1. Rents drop 10%
    2. Rents drop 20%
    3. Cap rates expand 100bps (exit values decline)
    4. Cap rates expand 200bps
    5. Combined: rents -10% AND cap rate +100bps
    """
    sub = df.loc[portfolio].copy()
    base_cost = sub["total_cost"].sum()
    base_mv = sub["est_market_value"].sum()

    scenarios = {}

    # Scenario 1: Rents -10%
    rent_shock = 0.90
    noi_adj = sub["noi_stabilized"] * rent_shock
    scenarios["rents_down_10pct"] = {
        "total_noi_stabilized": noi_adj.sum(),
        "cash_yield": noi_adj.sum() / base_cost if base_cost else 0,
        "blended_cap": (sub["noi_current"] * rent_shock).sum() / base_mv if base_mv else 0,
    }

    # Scenario 2: Rents -20%
    rent_shock2 = 0.80
    noi_adj2 = sub["noi_stabilized"] * rent_shock2
    scenarios["rents_down_20pct"] = {
        "total_noi_stabilized": noi_adj2.sum(),
        "cash_yield": noi_adj2.sum() / base_cost if base_cost else 0,
        "blended_cap": (sub["noi_current"] * rent_shock2).sum() / base_mv if base_mv else 0,
    }

    # Scenario 3: Exit cap rates expand 100bps
    # Approximate: if exit cap expands, exit value shrinks proportionally
    # exit_value = NOI_stabilized / exit_cap. If exit_cap goes up by 1%, exit_value drops.
    # We estimate by reducing exit_value by ~12% (rough for 100bps expansion on ~8% cap)
    exit_haircut = 0.88
    exit_adj = sub["exit_value"] * exit_haircut
    scenarios["cap_expand_100bps"] = {
        "total_exit_value": exit_adj.sum(),
        "equity_multiple": exit_adj.sum() / base_cost if base_cost else 0,
    }

    # Scenario 4: Exit cap rates expand 200bps
    exit_haircut2 = 0.78
    exit_adj2 = sub["exit_value"] * exit_haircut2
    scenarios["cap_expand_200bps"] = {
        "total_exit_value": exit_adj2.sum(),
        "equity_multiple": exit_adj2.sum() / base_cost if base_cost else 0,
    }

    # Scenario 5: Combined stress
    noi_stress = sub["noi_stabilized"] * 0.90
    exit_stress = sub["exit_value"] * 0.88
    scenarios["combined_stress"] = {
        "total_noi_stabilized": noi_stress.sum(),
        "cash_yield": noi_stress.sum() / base_cost if base_cost else 0,
        "total_exit_value": exit_stress.sum(),
        "equity_multiple": exit_stress.sum() / base_cost if base_cost else 0,
    }

    return scenarios


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------
def fmt_dollar(v):
    """Format a number as a dollar amount."""
    if abs(v) >= 1_000_000:
        return f"${v/1_000_000:,.2f}M"
    elif abs(v) >= 1_000:
        return f"${v/1_000:,.1f}K"
    else:
        return f"${v:,.0f}"


def fmt_pct(v):
    return f"{v*100:.2f}%"


def print_section(title):
    width = 80
    print()
    print("=" * width)
    print(f"  {title}")
    print("=" * width)


def print_portfolio(portfolio: list, df: pd.DataFrame, budget: float,
                    label: str = "OPTIMIZED PORTFOLIO"):
    """Print full portfolio report."""
    metrics = compute_metrics(portfolio, df)
    sub = df.loc[portfolio].sort_values("selection_score", ascending=False)

    print_section(f"{label} — Forte Investment Fund")
    print(f"  Budget: {fmt_dollar(budget)}")
    print(f"  Properties: {metrics['num_properties']}  |  Units: {metrics['total_units']}")
    print(f"  Total Cost: {fmt_dollar(metrics['total_cost'])}  "
          f"({fmt_pct(metrics['total_cost']/budget)} of budget)")
    print(f"  Market Value: {fmt_dollar(metrics['total_market_value'])}  |  "
          f"Reno: {fmt_dollar(metrics['total_reno'])}")
    print()
    print(f"  --- Returns ---")
    print(f"  Blended Cap Rate (current):     {fmt_pct(metrics['blended_cap_rate'])}")
    print(f"  Blended Cap Rate (stabilized):  {fmt_pct(metrics['blended_cap_rate_stabilized'])}")
    print(f"  Cash-on-Cash Yield:             {fmt_pct(metrics['cash_on_cash_yield'])}")
    print(f"  Blended Equity Multiple:        {metrics['blended_equity_multiple']:.2f}x")
    print(f"  Total NOI (current):            {fmt_dollar(metrics['total_noi_current'])}")
    print(f"  Total NOI (stabilized):         {fmt_dollar(metrics['total_noi_stabilized'])}")
    print(f"  Total Gross Rent:               {fmt_dollar(metrics['total_gross_rent'])}")
    print(f"  Total Exit Value:               {fmt_dollar(metrics['total_exit_value'])}")
    print(f"  Total Annual Tax:               {fmt_dollar(metrics['total_annual_tax'])}")
    print(f"  Avg Composite Score:            {metrics['avg_composite_score']:.4f}")
    print(f"  Avg Selection Score:            {metrics['avg_selection_score']:.4f}")

    # Property list
    print_section("PROPERTY LIST")
    print(f"{'#':>3}  {'Address':<30} {'ZIP':<6} {'Class':<5} {'Units':>5} "
          f"{'Total Cost':>12} {'NOI Stab':>10} {'Cap Rate':>8} {'Eq Mult':>8} "
          f"{'Score':>7} {'Distress':>8} {'OZ':>3} {'Sheriff':>7}")
    print("-" * 140)

    for i, (idx, row) in enumerate(sub.iterrows(), 1):
        addr = str(row["address"])[:28]
        oz = "Y" if row["in_opportunity_zone"] else ""
        sheriff = "Y" if row["in_sheriff_sale"] else ""
        print(f"{i:>3}  {addr:<30} {row['zip']:<6} {row['prop_class']:<5} "
              f"{row['est_units']:>5} "
              f"{fmt_dollar(row['total_cost']):>12} "
              f"{fmt_dollar(row['noi_stabilized']):>10} "
              f"{fmt_pct(row['cap_rate_current']):>8} "
              f"{row['equity_multiple']:>7.2f}x "
              f"{row['composite_score']:>7.4f} "
              f"{row['distress_score']:>8} "
              f"{oz:>3} {sheriff:>7}")

    # Diversification breakdown
    print_section("DIVERSIFICATION — BY ZIP CODE")
    zip_group = sub.groupby("zip").agg(
        count=("address", "count"),
        total_cost=("total_cost", "sum"),
        total_units=("est_units", "sum"),
        avg_cap=("cap_rate_current", "mean"),
    ).sort_values("total_cost", ascending=False)

    for zip_code, row in zip_group.iterrows():
        pct = row["total_cost"] / metrics["total_cost"]
        budget_pct = row["total_cost"] / budget
        print(f"  ZIP {zip_code}: {int(row['count'])} props, "
              f"{int(row['total_units'])} units, "
              f"{fmt_dollar(row['total_cost'])} "
              f"({fmt_pct(pct)} of portfolio, {fmt_pct(budget_pct)} of budget), "
              f"avg cap {fmt_pct(row['avg_cap'])}")

    print_section("DIVERSIFICATION — BY PROPERTY CLASS")
    class_group = sub.groupby("prop_class_desc").agg(
        count=("address", "count"),
        total_cost=("total_cost", "sum"),
        total_units=("est_units", "sum"),
    ).sort_values("total_cost", ascending=False)

    for cls, row in class_group.iterrows():
        pct = row["total_cost"] / metrics["total_cost"]
        budget_pct = row["total_cost"] / budget
        print(f"  {cls}: {int(row['count'])} props, "
              f"{int(row['total_units'])} units, "
              f"{fmt_dollar(row['total_cost'])} "
              f"({fmt_pct(pct)} of portfolio, {fmt_pct(budget_pct)} of budget)")

    return metrics


def print_sensitivity(portfolio: list, df: pd.DataFrame, base_metrics: dict):
    """Print sensitivity analysis."""
    print_section("SENSITIVITY ANALYSIS")
    scenarios = sensitivity_analysis(portfolio, df)

    base_yield = base_metrics["cash_on_cash_yield"]
    base_eq = base_metrics["blended_equity_multiple"]
    base_noi = base_metrics["total_noi_stabilized"]
    base_exit = base_metrics["total_exit_value"]

    print(f"  {'Scenario':<30} {'NOI Stab':>14} {'Cash Yield':>12} "
          f"{'Exit Value':>14} {'Eq Multiple':>12}")
    print(f"  {'-'*30} {'-'*14} {'-'*12} {'-'*14} {'-'*12}")

    print(f"  {'BASE CASE':<30} {fmt_dollar(base_noi):>14} "
          f"{fmt_pct(base_yield):>12} "
          f"{fmt_dollar(base_exit):>14} "
          f"{base_eq:>11.2f}x")

    for name, data in scenarios.items():
        noi_str = fmt_dollar(data.get("total_noi_stabilized", base_noi))
        cy_str = fmt_pct(data.get("cash_yield", base_yield))
        exit_str = fmt_dollar(data.get("total_exit_value", base_exit))
        eq_str = f"{data.get('equity_multiple', base_eq):.2f}x"
        label = name.replace("_", " ").title()
        print(f"  {label:<30} {noi_str:>14} {cy_str:>12} {exit_str:>14} {eq_str:>12}")


def print_comparison(opt_metrics: dict, naive_metrics: dict):
    """Print comparison between optimized and naive portfolios."""
    print_section("OPTIMIZED vs NAIVE COMPARISON")

    fields = [
        ("Properties", "num_properties", "d"),
        ("Total Units", "total_units", "d"),
        ("Total Cost", "total_cost", "$"),
        ("Blended Cap Rate", "blended_cap_rate", "%"),
        ("Cap Rate (Stabilized)", "blended_cap_rate_stabilized", "%"),
        ("Cash-on-Cash Yield", "cash_on_cash_yield", "%"),
        ("Equity Multiple", "blended_equity_multiple", "x"),
        ("NOI (Current)", "total_noi_current", "$"),
        ("NOI (Stabilized)", "total_noi_stabilized", "$"),
        ("Exit Value", "total_exit_value", "$"),
        ("Avg Composite Score", "avg_composite_score", "f"),
    ]

    print(f"  {'Metric':<28} {'Optimized':>16} {'Naive (Top N)':>16} {'Delta':>12}")
    print(f"  {'-'*28} {'-'*16} {'-'*16} {'-'*12}")

    for label, key, fmt in fields:
        opt_v = opt_metrics[key]
        naive_v = naive_metrics[key]

        if fmt == "$":
            opt_s = fmt_dollar(opt_v)
            naive_s = fmt_dollar(naive_v)
            if naive_v != 0:
                delta = f"{(opt_v/naive_v - 1)*100:+.1f}%"
            else:
                delta = "N/A"
        elif fmt == "%":
            opt_s = fmt_pct(opt_v)
            naive_s = fmt_pct(naive_v)
            delta = f"{(opt_v - naive_v)*10000:+.0f}bps"
        elif fmt == "x":
            opt_s = f"{opt_v:.2f}x"
            naive_s = f"{naive_v:.2f}x"
            delta = f"{opt_v - naive_v:+.2f}x"
        elif fmt == "d":
            opt_s = f"{int(opt_v)}"
            naive_s = f"{int(naive_v)}"
            delta = f"{int(opt_v - naive_v):+d}"
        else:
            opt_s = f"{opt_v:.4f}"
            naive_s = f"{naive_v:.4f}"
            delta = f"{opt_v - naive_v:+.4f}"

        print(f"  {label:<28} {opt_s:>16} {naive_s:>16} {delta:>12}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Forte Investment Fund — Paterson NJ Portfolio Optimizer")
    parser.add_argument("--budget", type=float, default=DEFAULT_BUDGET,
                        help=f"Total acquisition budget (default: ${DEFAULT_BUDGET:,.0f})")
    parser.add_argument("--min-props", type=int, default=MIN_PROPERTIES,
                        help=f"Minimum properties in portfolio (default: {MIN_PROPERTIES})")
    parser.add_argument("--max-props", type=int, default=MAX_PROPERTIES,
                        help=f"Maximum properties in portfolio (default: {MAX_PROPERTIES})")
    parser.add_argument("--oz-only", action="store_true",
                        help="Only consider Opportunity Zone properties")
    parser.add_argument("--no-naive", action="store_true",
                        help="Skip naive portfolio comparison")
    parser.add_argument("--csv", type=str, default=None,
                        help="Export optimized portfolio to CSV")
    args = parser.parse_args()

    budget = args.budget
    min_props = args.min_props
    max_props = args.max_props

    # Load data
    if not DATA_PATH.exists():
        print(f"ERROR: Data file not found at {DATA_PATH}")
        sys.exit(1)

    df = pd.read_csv(DATA_PATH)
    print(f"Loaded {len(df):,} properties from {DATA_PATH.name}")

    # --- Filtering ---
    eligible = df.copy()

    # Hard filter: equity multiple > 1.0
    eligible = eligible[eligible["equity_multiple"] > MIN_EQUITY_MULTIPLE]
    print(f"  After equity_multiple > {MIN_EQUITY_MULTIPLE}: {len(eligible):,}")

    # Hard filter: positive NOI
    eligible = eligible[eligible["noi_stabilized"] > 0]
    print(f"  After positive NOI (stabilized): {len(eligible):,}")

    # Hard filter: total_cost must be positive and <= budget
    eligible = eligible[(eligible["total_cost"] > 0) & (eligible["total_cost"] <= budget)]
    print(f"  After cost in range (0, {fmt_dollar(budget)}]: {len(eligible):,}")

    # Optional: OZ only
    if args.oz_only:
        eligible = eligible[eligible["in_opportunity_zone"] == True]
        print(f"  After Opportunity Zone filter: {len(eligible):,}")

    if len(eligible) < min_props:
        print(f"\nERROR: Only {len(eligible)} eligible properties, need at least {min_props}.")
        sys.exit(1)

    # Compute selection score
    eligible = eligible.copy()
    eligible["selection_score"] = eligible.apply(compute_selection_score, axis=1)
    print(f"\nSelection scores: min={eligible['selection_score'].min():.4f}, "
          f"median={eligible['selection_score'].median():.4f}, "
          f"max={eligible['selection_score'].max():.4f}")

    # --- Greedy Selection ---
    print("\nRunning greedy selection...")
    portfolio = greedy_select(eligible, budget, min_props, max_props)
    print(f"  Greedy selected {len(portfolio)} properties, "
          f"cost: {fmt_dollar(eligible.loc[portfolio, 'total_cost'].sum())}")

    # --- Swap Improvement ---
    print("Running swap improvement...")
    portfolio = swap_improve(portfolio, eligible, budget, min_props, max_props)
    print(f"  After swaps: {len(portfolio)} properties, "
          f"cost: {fmt_dollar(eligible.loc[portfolio, 'total_cost'].sum())}")

    # --- Print Results ---
    opt_metrics = print_portfolio(portfolio, eligible, budget, "OPTIMIZED PORTFOLIO")

    # Sensitivity
    print_sensitivity(portfolio, eligible, opt_metrics)

    # Naive comparison
    if not args.no_naive:
        naive_portfolio = naive_select(eligible, budget, max_props)
        naive_metrics = print_portfolio(naive_portfolio, eligible, budget,
                                        "NAIVE PORTFOLIO (Top N by Composite Score)")
        print_comparison(opt_metrics, naive_metrics)

    # Export CSV
    if args.csv:
        export_cols = [
            "address", "zip", "block_lot", "prop_class", "prop_class_desc",
            "est_units", "est_market_value", "est_reno_cost", "total_cost",
            "noi_current", "noi_stabilized", "cap_rate_current",
            "equity_multiple", "exit_value", "gross_annual_rent", "annual_tax",
            "distress_score", "distress_flags", "in_opportunity_zone",
            "in_sheriff_sale", "absentee_status", "composite_score",
            "selection_score",
        ]
        out = eligible.loc[portfolio, export_cols].sort_values(
            "selection_score", ascending=False)
        out.to_csv(args.csv, index=False)
        print(f"\nExported portfolio to {args.csv}")

    return portfolio, opt_metrics


if __name__ == "__main__":
    main()
