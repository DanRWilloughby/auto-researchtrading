"""
Coinbase API auth smoke test.
Verifies:
  1. API key loads and authenticates
  2. Account / portfolio data is reachable
  3. Perpetual futures products are visible
  4. Perps portfolio balance is accessible
No orders are placed. Read-only calls only.
"""
import json
import sys
from pathlib import Path

KEY_FILE = Path(__file__).resolve().parents[1] / "secrets" / "coinbase_api.json"

if not KEY_FILE.exists():
    print(f"ERROR: key file not found at {KEY_FILE}")
    sys.exit(1)

with open(KEY_FILE) as f:
    key_data = json.load(f)

try:
    from coinbase.rest import RESTClient
except ImportError:
    print("ERROR: coinbase-advanced-py not installed in this venv")
    sys.exit(1)

client = RESTClient(
    api_key=key_data["name"],
    api_secret=key_data["privateKey"],
)

print("=" * 70)
print("  COINBASE API AUTH SMOKE TEST")
print("=" * 70)

# Test 1: List accounts
print("\n[1/4] Listing accounts...")
try:
    accounts = client.get_accounts()
    acct_list = accounts.get("accounts", []) if isinstance(accounts, dict) else accounts.accounts
    print(f"  ✅ Authenticated. Found {len(acct_list)} accounts.")
    non_zero = [a for a in acct_list if float(
        (a.get("available_balance", {}) if isinstance(a, dict) else a.available_balance).get("value", 0)
        if isinstance(a.get("available_balance", {}) if isinstance(a, dict) else a.available_balance, dict)
        else 0
    ) > 0]
    print(f"  Non-zero balances: {len(non_zero)}")
except Exception as e:
    print(f"  ❌ FAILED: {type(e).__name__}: {e}")
    sys.exit(1)

# Test 2: List portfolios
print("\n[2/4] Listing portfolios...")
try:
    portfolios = client.get_portfolios()
    plist = portfolios.get("portfolios", []) if isinstance(portfolios, dict) else portfolios.portfolios
    for p in plist:
        pd = p if isinstance(p, dict) else p.__dict__
        name = pd.get("name", "?")
        ptype = pd.get("type", "?")
        uuid = pd.get("uuid", "?")
        print(f"  • {name} ({ptype})  uuid={uuid[:8]}...")
    print(f"  ✅ Found {len(plist)} portfolios.")
except Exception as e:
    print(f"  ❌ FAILED: {type(e).__name__}: {e}")

# Test 3: List perp products
print("\n[3/4] Checking perpetual futures products (BTC/ETH/SOL)...")
try:
    products = client.get_products(product_type="FUTURE", contract_expiry_type="PERPETUAL")
    plist = products.get("products", []) if isinstance(products, dict) else products.products
    target = ("BTC", "ETH", "SOL")
    found = {}
    for p in plist:
        pd = p if isinstance(p, dict) else p.__dict__
        pid = pd.get("product_id", "")
        base = pd.get("base_display_symbol", "")
        if base in target and base not in found:
            found[base] = {
                "product_id": pid,
                "display": pd.get("display_name", ""),
                "base_increment": pd.get("base_increment", ""),
                "status": pd.get("status", ""),
            }
    for coin in target:
        if coin in found:
            info = found[coin]
            print(f"  ✅ {coin}: {info['product_id']} | increment={info['base_increment']} | status={info['status']}")
        else:
            print(f"  ⚠️  {coin}: NOT FOUND in perp products")
    print(f"  Total perp products available: {len(plist)}")
except Exception as e:
    print(f"  ❌ FAILED: {type(e).__name__}: {e}")

# Test 4: Perps portfolio balance
print("\n[4/4] Checking perps portfolio balance...")
try:
    # Find the INTX / perps portfolio
    plist = portfolios.get("portfolios", []) if isinstance(portfolios, dict) else portfolios.portfolios
    perp_portfolio_uuid = None
    for p in plist:
        pd = p if isinstance(p, dict) else p.__dict__
        ptype = pd.get("type", "")
        if "INTX" in ptype or "PERP" in ptype or "FUTURES" in ptype.upper():
            perp_portfolio_uuid = pd.get("uuid")
            print(f"  Found perps-like portfolio: {pd.get('name')} (type={ptype})")
            break

    if perp_portfolio_uuid:
        try:
            summary = client.get_perps_portfolio_summary(portfolio_uuid=perp_portfolio_uuid)
            sd = summary if isinstance(summary, dict) else summary.__dict__
            print(f"  ✅ Perps portfolio summary retrieved.")
            bal = sd.get("portfolio_balances", {}) or sd.get("total_balance", {})
            if bal:
                print(f"     Balance data: {json.dumps(bal, default=str, indent=2)[:300]}")
        except Exception as e:
            print(f"  ⚠️  get_perps_portfolio_summary failed: {e}")
            print(f"     (This may be expected if perps aren't activated yet)")
    else:
        print(f"  ⚠️  No separate perps/INTX portfolio found.")
        print(f"     This may be normal — Coinbase US perps may live under the default portfolio.")
        print(f"     We'll query perp positions directly in the build.")
except Exception as e:
    print(f"  ❌ FAILED: {type(e).__name__}: {e}")

print("\n" + "=" * 70)
print("  SMOKE TEST COMPLETE")
print("=" * 70)
