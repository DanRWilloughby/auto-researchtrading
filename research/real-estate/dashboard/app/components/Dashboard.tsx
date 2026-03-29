"use client";

import { useState, useMemo, useCallback } from "react";
import { Property, Filters, SortField, SortDirection } from "../types";
import SummaryCards from "./SummaryCards";
import FilterSidebar from "./FilterSidebar";
import DataTable from "./DataTable";

interface DashboardProps {
  properties: Property[];
  dataDate: string;
}

const defaultFilters: Filters = {
  propClass: "",
  minUnits: "",
  maxUnits: "",
  minCapRate: "",
  absenteeOnly: false,
  sheriffSaleOnly: false,
  minDistressScore: "",
  minCompositeScore: "",
  searchAddress: "",
};

const PAGE_SIZE = 50;

export default function Dashboard({ properties, dataDate }: DashboardProps) {
  const [filters, setFilters] = useState<Filters>(defaultFilters);
  const [sortField, setSortField] = useState<SortField>("composite_score");
  const [sortDirection, setSortDirection] = useState<SortDirection>("desc");
  const [page, setPage] = useState(0);
  const [sidebarOpen, setSidebarOpen] = useState(true);

  // Extract unique property classes for the filter dropdown
  const propClasses = useMemo(() => {
    const classes = new Set<string>();
    properties.forEach((p) => {
      if (p.prop_class_desc) classes.add(p.prop_class_desc);
    });
    return Array.from(classes).sort();
  }, [properties]);

  // Apply filters
  const filtered = useMemo(() => {
    return properties.filter((p) => {
      if (
        filters.searchAddress &&
        !p.address.toLowerCase().includes(filters.searchAddress.toLowerCase())
      )
        return false;
      if (filters.propClass && p.prop_class_desc !== filters.propClass)
        return false;
      if (filters.minUnits && (p.est_units ?? 0) < Number(filters.minUnits))
        return false;
      if (
        filters.maxUnits &&
        (p.est_units ?? Infinity) > Number(filters.maxUnits)
      )
        return false;
      if (
        filters.minCapRate &&
        (p.cap_rate_current ?? 0) < Number(filters.minCapRate) / 100
      )
        return false;
      if (
        filters.absenteeOnly &&
        (!p.absentee_status || p.absentee_status === "owner_occupied")
      )
        return false;
      if (filters.sheriffSaleOnly && !p.in_sheriff_sale) return false;
      if (
        filters.minDistressScore &&
        (p.distress_score ?? 0) < Number(filters.minDistressScore)
      )
        return false;
      if (
        filters.minCompositeScore &&
        (p.composite_score ?? 0) < Number(filters.minCompositeScore)
      )
        return false;
      return true;
    });
  }, [properties, filters]);

  // Apply sort
  const sorted = useMemo(() => {
    return [...filtered].sort((a, b) => {
      const aVal = a[sortField];
      const bVal = b[sortField];

      // Handle nulls
      if (aVal === null && bVal === null) return 0;
      if (aVal === null) return 1;
      if (bVal === null) return -1;

      // Handle booleans
      if (typeof aVal === "boolean" && typeof bVal === "boolean") {
        const diff = (aVal ? 1 : 0) - (bVal ? 1 : 0);
        return sortDirection === "asc" ? diff : -diff;
      }

      // Handle strings
      if (typeof aVal === "string" && typeof bVal === "string") {
        const cmp = aVal.localeCompare(bVal);
        return sortDirection === "asc" ? cmp : -cmp;
      }

      // Handle numbers
      if (typeof aVal === "number" && typeof bVal === "number") {
        return sortDirection === "asc" ? aVal - bVal : bVal - aVal;
      }

      return 0;
    });
  }, [filtered, sortField, sortDirection]);

  const handleSort = useCallback(
    (field: SortField) => {
      if (field === sortField) {
        setSortDirection((d) => (d === "asc" ? "desc" : "asc"));
      } else {
        setSortField(field);
        setSortDirection("desc");
      }
      setPage(0);
    },
    [sortField]
  );

  const handleFilterChange = useCallback((newFilters: Filters) => {
    setFilters(newFilters);
    setPage(0);
  }, []);

  const handleReset = useCallback(() => {
    setFilters(defaultFilters);
    setPage(0);
  }, []);

  return (
    <div className="flex flex-col flex-1 min-h-screen">
      {/* Header */}
      <header className="border-b border-card-border bg-card-bg/80 backdrop-blur-sm sticky top-0 z-20">
        <div className="max-w-[1920px] mx-auto px-4 md:px-6 py-4 flex items-center justify-between">
          <div>
            <h1 className="text-xl md:text-2xl font-bold text-foreground tracking-tight">
              Paterson Deal Intelligence
            </h1>
            <p className="text-sm text-muted mt-0.5">
              <span className="text-accent font-medium">Veloce Capital</span>
              <span className="mx-2 text-card-border">|</span>
              Data as of {dataDate}
            </p>
          </div>
          <button
            onClick={() => setSidebarOpen(!sidebarOpen)}
            className="md:hidden px-3 py-2 rounded-lg bg-input-bg border border-input-border text-foreground text-sm cursor-pointer"
          >
            {sidebarOpen ? "Hide Filters" : "Show Filters"}
          </button>
        </div>
      </header>

      {/* Main content */}
      <div className="flex-1 max-w-[1920px] mx-auto w-full px-4 md:px-6 py-6">
        {/* Summary cards - full width */}
        <div className="mb-6">
          <SummaryCards
            properties={properties}
            filteredCount={sorted.length}
          />
        </div>

        {/* Sidebar + Table layout */}
        <div className="flex gap-6">
          {/* Sidebar */}
          <div
            className={`${
              sidebarOpen ? "block" : "hidden"
            } md:block w-full md:w-64 lg:w-72 flex-shrink-0`}
          >
            <div className="sticky top-[85px]">
              <FilterSidebar
                filters={filters}
                setFilters={handleFilterChange}
                propClasses={propClasses}
                onReset={handleReset}
              />
            </div>
          </div>

          {/* Table */}
          <div className="flex-1 min-w-0">
            <DataTable
              properties={sorted}
              sortField={sortField}
              sortDirection={sortDirection}
              onSort={handleSort}
              page={page}
              pageSize={PAGE_SIZE}
              onPageChange={setPage}
            />
          </div>
        </div>
      </div>

      {/* Footer */}
      <footer className="border-t border-card-border bg-card-bg/50 py-4">
        <div className="max-w-[1920px] mx-auto px-4 md:px-6 text-center text-xs text-muted">
          Veloce Capital Deal Intelligence Platform &middot; Paterson NJ
          Multifamily Analysis &middot; Internal Use Only
        </div>
      </footer>
    </div>
  );
}
