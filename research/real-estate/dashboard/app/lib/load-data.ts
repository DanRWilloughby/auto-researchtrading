import fs from "fs";
import path from "path";
import Papa from "papaparse";
import { Property } from "../types";

export function loadProperties(): Property[] {
  const csvPath = path.join(process.cwd(), "data", "paterson_ranked_deals.csv");
  const csvContent = fs.readFileSync(csvPath, "utf-8");

  const { data } = Papa.parse(csvContent, {
    header: true,
    skipEmptyLines: true,
    dynamicTyping: false,
  });

  return (data as Record<string, string>[]).map((row) => ({
    address: row.address || "",
    zip: row.zip || "",
    block_lot: row.block_lot || "",
    prop_class: row.prop_class || "",
    prop_class_desc: row.prop_class_desc || "",
    year_built: parseNum(row.year_built),
    est_units: parseNum(row.est_units),
    sqft: parseNum(row.sqft),
    net_assessed: parseNum(row.net_assessed),
    est_market_value: parseNum(row.est_market_value),
    last_sale_price: parseNum(row.last_sale_price),
    annual_tax: parseNum(row.annual_tax),
    absentee_status: row.absentee_status || "",
    owner_location: row.owner_location || "",
    in_sheriff_sale: row.in_sheriff_sale === "True",
    est_rent_per_unit: parseNum(row.est_rent_per_unit),
    gross_annual_rent: parseNum(row.gross_annual_rent),
    noi_current: parseNum(row.noi_current),
    cap_rate_current: parseNum(row.cap_rate_current),
    rent_to_price: parseNum(row.rent_to_price),
    est_reno_cost: parseNum(row.est_reno_cost),
    total_cost: parseNum(row.total_cost),
    noi_stabilized: parseNum(row.noi_stabilized),
    exit_value: parseNum(row.exit_value),
    equity_multiple: parseNum(row.equity_multiple),
    cap_rate_spread: parseNum(row.cap_rate_spread),
    distress_score: parseNum(row.distress_score),
    distress_flags: row.distress_flags || "",
    composite_score: parseNum(row.composite_score),
  }));
}

function parseNum(val: string | undefined): number | null {
  if (!val || val === "" || val === "None" || val === "nan") return null;
  const n = Number(val);
  return isNaN(n) ? null : n;
}
