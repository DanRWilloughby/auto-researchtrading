"""Find where Coinbase US perpetual-style futures are exposed in the API."""
import json
from pathlib import Path
from coinbase.rest import RESTClient

KEY_FILE = Path(__file__).resolve().parents[1] / "secrets" / "coinbase_api.json"
with open(KEY_FILE) as f:
    key_data = json.load(f)

client = RESTClient(api_key=key_data["name"], api_secret=key_data["privateKey"])

def to_dict(obj):
    return obj if isinstance(obj, dict) else obj.__dict__

print("=" * 70)
print("  FINDING COINBASE US PERPS")
print("=" * 70)

# Approach 1: All FUTURE products (no expiry filter)
print("\n[1] Looking at ALL FUTURE product types (no expiry filter)...")
try:
    resp = client.get_products(product_type="FUTURE")
    products = resp.get("products", []) if isinstance(resp, dict) else resp.products
    print(f"   Total FUTURE products: {len(products)}")
    # Show samples with BTC/ETH/SOL
    matches = []
    for p in products:
        pd = to_dict(p)
        base = pd.get("base_display_symbol", "") or pd.get("base_name", "")
        pid = pd.get("product_id", "")
        if any(coin in str(pid).upper() or coin in str(base).upper() for coin in ["BTC", "BIT", "ETH", "SOL"]):
            matches.append(pd)
    print(f"   Matching BTC/ETH/SOL: {len(matches)}")
    for m in matches[:15]:
        pid = m.get("product_id", "")
        display = m.get("display_name", "")
        status = m.get("status", "")
        expiry = m.get("future_product_details", {}) if isinstance(m.get("future_product_details"), dict) else {}
        contract_expiry = m.get("contract_expiry_type", "")
        print(f"   • {pid:<30} | {display:<35} | expiry_type={contract_expiry} | status={status}")
except Exception as e:
    print(f"   Error: {e}")

# Approach 2: Look at ALL products and filter by "PERP" in the ID or name
print("\n[2] Searching ALL products for 'PERP' keyword...")
try:
    resp = client.get_products()
    products = resp.get("products", []) if isinstance(resp, dict) else resp.products
    print(f"   Total products: {len(products)}")
    perp_matches = []
    for p in products:
        pd = to_dict(p)
        pid = str(pd.get("product_id", ""))
        display = str(pd.get("display_name", ""))
        if "PERP" in pid.upper() or "PERP" in display.upper():
            perp_matches.append(pd)
    print(f"   Products with 'PERP' in id/name: {len(perp_matches)}")
    # Filter to BTC/ETH/SOL
    target = [m for m in perp_matches if any(
        c in str(m.get("product_id", "")).upper() for c in ["BTC", "ETH", "SOL"]
    )]
    for m in target[:15]:
        pid = m.get("product_id", "")
        display = m.get("display_name", "")
        status = m.get("status", "")
        print(f"   • {pid:<30} | {display:<35} | status={status}")
except Exception as e:
    print(f"   Error: {e}")

# Approach 3: Check for CFM / Nano contracts directly
print("\n[3] Looking for nano/CFM contracts (US perp-style futures)...")
try:
    resp = client.get_products(product_type="FUTURE")
    products = resp.get("products", []) if isinstance(resp, dict) else resp.products
    # Coinbase nano contracts use ticker like "BIT" (BTC nano), "NET" (ETH nano), etc.
    # Or they may use the expiry-based naming: BIT-29DEC30-CDE, etc.
    nano_matches = []
    for p in products:
        pd = to_dict(p)
        pid = str(pd.get("product_id", ""))
        display = str(pd.get("display_name", ""))
        # Look for nano-style identifiers
        if any(pattern in pid for pattern in ["NANO", "BIT-", "NET-", "SOL-", "CDE", "2030"]):
            nano_matches.append(pd)
    # Only BTC/ETH/SOL
    target = [m for m in nano_matches if any(
        c in str(m.get("product_id", "")).upper() or c in str(m.get("display_name", "")).upper()
        for c in ["BITCOIN", "ETHER", "SOLANA", "BTC", "ETH", "SOL"]
    )][:30]
    print(f"   Matches: {len(target)}")
    for m in target:
        pid = m.get("product_id", "")
        display = m.get("display_name", "")
        status = m.get("status", "")
        expiry_type = m.get("contract_expiry_type", "")
        print(f"   • {pid:<35} | {display:<40} | expiry={expiry_type} | status={status}")
except Exception as e:
    print(f"   Error: {e}")

# Approach 4: Raw dump of first 5 FUTURE products to see full structure
print("\n[4] Raw dump of first 3 FUTURE products (to understand schema)...")
try:
    resp = client.get_products(product_type="FUTURE")
    products = resp.get("products", []) if isinstance(resp, dict) else resp.products
    for p in products[:3]:
        pd = to_dict(p)
        print(f"\n   Product: {pd.get('product_id')}")
        for k, v in pd.items():
            if k in ("product_id", "display_name", "base_display_symbol", "base_name",
                     "contract_expiry_type", "product_type", "status", "expiration_date",
                     "future_product_details", "quote_currency_id", "base_currency_id"):
                print(f"     {k}: {v}")
except Exception as e:
    print(f"   Error: {e}")
