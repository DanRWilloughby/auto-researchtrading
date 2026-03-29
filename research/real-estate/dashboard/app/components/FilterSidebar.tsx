"use client";

import { Filters } from "../types";

interface FilterSidebarProps {
  filters: Filters;
  setFilters: (filters: Filters) => void;
  propClasses: string[];
  onReset: () => void;
}

export default function FilterSidebar({
  filters,
  setFilters,
  propClasses,
  onReset,
}: FilterSidebarProps) {
  const update = (field: keyof Filters, value: string | boolean) => {
    setFilters({ ...filters, [field]: value });
  };

  const inputClass =
    "w-full bg-input-bg border border-input-border rounded-lg px-3 py-2 text-sm text-foreground focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent transition-colors";
  const labelClass =
    "block text-xs font-medium text-muted uppercase tracking-wide mb-1.5";

  return (
    <div className="bg-card-bg border border-card-border rounded-xl p-5 flex flex-col gap-5">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-foreground uppercase tracking-wide">
          Filters
        </h2>
        <button
          onClick={onReset}
          className="text-xs text-accent hover:text-accent/80 transition-colors cursor-pointer"
        >
          Reset All
        </button>
      </div>

      {/* Address search */}
      <div>
        <label className={labelClass}>Search Address</label>
        <input
          type="text"
          value={filters.searchAddress}
          onChange={(e) => update("searchAddress", e.target.value)}
          placeholder="123 Main St..."
          className={inputClass}
        />
      </div>

      {/* Property class */}
      <div>
        <label className={labelClass}>Property Class</label>
        <select
          value={filters.propClass}
          onChange={(e) => update("propClass", e.target.value)}
          className={inputClass}
        >
          <option value="">All Classes</option>
          {propClasses.map((pc) => (
            <option key={pc} value={pc}>
              {pc}
            </option>
          ))}
        </select>
      </div>

      {/* Units range */}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className={labelClass}>Min Units</label>
          <input
            type="number"
            value={filters.minUnits}
            onChange={(e) => update("minUnits", e.target.value)}
            placeholder="0"
            min="0"
            className={inputClass}
          />
        </div>
        <div>
          <label className={labelClass}>Max Units</label>
          <input
            type="number"
            value={filters.maxUnits}
            onChange={(e) => update("maxUnits", e.target.value)}
            placeholder="Any"
            min="0"
            className={inputClass}
          />
        </div>
      </div>

      {/* Min cap rate */}
      <div>
        <label className={labelClass}>Min Cap Rate (%)</label>
        <input
          type="number"
          value={filters.minCapRate}
          onChange={(e) => update("minCapRate", e.target.value)}
          placeholder="0"
          step="0.1"
          min="0"
          className={inputClass}
        />
      </div>

      {/* Min distress score */}
      <div>
        <label className={labelClass}>Min Distress Score</label>
        <input
          type="number"
          value={filters.minDistressScore}
          onChange={(e) => update("minDistressScore", e.target.value)}
          placeholder="0"
          min="0"
          max="10"
          step="1"
          className={inputClass}
        />
      </div>

      {/* Min composite score */}
      <div>
        <label className={labelClass}>Min Composite Score</label>
        <input
          type="number"
          value={filters.minCompositeScore}
          onChange={(e) => update("minCompositeScore", e.target.value)}
          placeholder="0"
          step="0.01"
          min="0"
          max="1"
          className={inputClass}
        />
      </div>

      {/* Toggle filters */}
      <div className="flex flex-col gap-3 pt-2 border-t border-card-border">
        <label className="flex items-center gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={filters.absenteeOnly}
            onChange={(e) => update("absenteeOnly", e.target.checked)}
            className="w-4 h-4 rounded border-input-border bg-input-bg text-accent focus:ring-accent cursor-pointer accent-accent"
          />
          <span className="text-sm text-foreground">Absentee Owners Only</span>
        </label>
        <label className="flex items-center gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={filters.sheriffSaleOnly}
            onChange={(e) => update("sheriffSaleOnly", e.target.checked)}
            className="w-4 h-4 rounded border-input-border bg-input-bg text-accent focus:ring-accent cursor-pointer accent-accent"
          />
          <span className="text-sm text-foreground">Sheriff Sales Only</span>
        </label>
      </div>
    </div>
  );
}
