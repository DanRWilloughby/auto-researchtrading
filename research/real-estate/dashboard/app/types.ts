export interface Property {
  address: string;
  zip: string;
  block_lot: string;
  prop_class: string;
  prop_class_desc: string;
  year_built: number | null;
  est_units: number | null;
  sqft: number | null;
  net_assessed: number | null;
  est_market_value: number | null;
  last_sale_price: number | null;
  annual_tax: number | null;
  absentee_status: string;
  owner_location: string;
  in_sheriff_sale: boolean;
  est_rent_per_unit: number | null;
  gross_annual_rent: number | null;
  noi_current: number | null;
  cap_rate_current: number | null;
  rent_to_price: number | null;
  est_reno_cost: number | null;
  total_cost: number | null;
  noi_stabilized: number | null;
  exit_value: number | null;
  equity_multiple: number | null;
  cap_rate_spread: number | null;
  distress_score: number | null;
  distress_flags: string;
  composite_score: number | null;
}

export interface Filters {
  propClass: string;
  minUnits: string;
  maxUnits: string;
  minCapRate: string;
  absenteeOnly: boolean;
  sheriffSaleOnly: boolean;
  minDistressScore: string;
  minCompositeScore: string;
  searchAddress: string;
}

export type SortField = keyof Property;
export type SortDirection = "asc" | "desc";
