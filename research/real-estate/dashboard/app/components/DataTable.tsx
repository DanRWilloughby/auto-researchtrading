"use client";

import { Property, SortField, SortDirection } from "../types";

interface DataTableProps {
  properties: Property[];
  sortField: SortField;
  sortDirection: SortDirection;
  onSort: (field: SortField) => void;
  page: number;
  pageSize: number;
  onPageChange: (page: number) => void;
}

function formatCurrency(val: number | null): string {
  if (val === null) return "--";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(val);
}

function formatPercent(val: number | null): string {
  if (val === null) return "--";
  return `${(val * 100).toFixed(1)}%`;
}

function formatNumber(val: number | null, decimals = 0): string {
  if (val === null) return "--";
  return val.toFixed(decimals);
}

function distressColor(score: number | null): string {
  if (score === null) return "text-muted";
  if (score >= 5) return "text-danger font-semibold";
  if (score >= 4) return "text-warning font-semibold";
  if (score >= 3) return "text-warning";
  return "text-muted";
}

function compositeColor(score: number | null): string {
  if (score === null) return "text-muted";
  if (score >= 0.95) return "text-success font-semibold";
  if (score >= 0.8) return "text-success";
  if (score >= 0.5) return "text-foreground";
  return "text-muted";
}

const columns: {
  key: SortField;
  label: string;
  align?: "right" | "center";
  render: (p: Property) => React.ReactNode;
  minWidth?: string;
}[] = [
  {
    key: "address",
    label: "Address",
    minWidth: "200px",
    render: (p) => (
      <div>
        <div className="font-medium text-foreground">{p.address}</div>
        <div className="text-xs text-muted">{p.zip}</div>
      </div>
    ),
  },
  {
    key: "prop_class_desc",
    label: "Class",
    render: (p) => (
      <span className="text-xs bg-accent-dim text-accent px-2 py-0.5 rounded-full whitespace-nowrap">
        {p.prop_class_desc || p.prop_class}
      </span>
    ),
  },
  {
    key: "est_units",
    label: "Units",
    align: "right",
    render: (p) => (
      <span className="font-mono">{p.est_units ?? "--"}</span>
    ),
  },
  {
    key: "est_market_value",
    label: "Market Value",
    align: "right",
    render: (p) => (
      <span className="font-mono">{formatCurrency(p.est_market_value)}</span>
    ),
  },
  {
    key: "cap_rate_current",
    label: "Cap Rate",
    align: "right",
    render: (p) => (
      <span
        className={`font-mono ${
          p.cap_rate_current !== null && p.cap_rate_current >= 0.08
            ? "text-success font-semibold"
            : p.cap_rate_current !== null && p.cap_rate_current >= 0.05
              ? "text-success"
              : "text-foreground"
        }`}
      >
        {formatPercent(p.cap_rate_current)}
      </span>
    ),
  },
  {
    key: "equity_multiple",
    label: "Equity Mult",
    align: "right",
    render: (p) => (
      <span
        className={`font-mono ${
          p.equity_multiple !== null && p.equity_multiple >= 5
            ? "text-success font-semibold"
            : "text-foreground"
        }`}
      >
        {p.equity_multiple !== null ? `${p.equity_multiple.toFixed(2)}x` : "--"}
      </span>
    ),
  },
  {
    key: "distress_score",
    label: "Distress",
    align: "center",
    render: (p) => (
      <div className="flex flex-col items-center">
        <span className={`font-mono ${distressColor(p.distress_score)}`}>
          {p.distress_score ?? "--"}
        </span>
      </div>
    ),
  },
  {
    key: "absentee_status",
    label: "Absentee",
    align: "center",
    render: (p) => {
      if (!p.absentee_status) return <span className="text-muted">--</span>;
      const label =
        p.absentee_status === "out_of_state"
          ? "Out-of-State"
          : p.absentee_status === "absentee_nj"
            ? "NJ"
            : p.absentee_status === "absentee_local"
              ? "Local"
              : p.absentee_status;
      return (
        <span className="text-xs bg-card-border text-foreground px-2 py-0.5 rounded-full whitespace-nowrap">
          {label}
        </span>
      );
    },
  },
  {
    key: "in_sheriff_sale",
    label: "Sheriff",
    align: "center",
    render: (p) =>
      p.in_sheriff_sale ? (
        <span className="text-xs bg-danger/20 text-danger px-2 py-0.5 rounded-full font-semibold">
          YES
        </span>
      ) : (
        <span className="text-muted">--</span>
      ),
  },
  {
    key: "composite_score",
    label: "Composite",
    align: "right",
    render: (p) => (
      <span className={`font-mono ${compositeColor(p.composite_score)}`}>
        {formatNumber(p.composite_score, 4)}
      </span>
    ),
  },
];

function SortIcon({
  field,
  sortField,
  sortDirection,
}: {
  field: SortField;
  sortField: SortField;
  sortDirection: SortDirection;
}) {
  if (field !== sortField)
    return <span className="text-muted/40 ml-1">&#x25B4;&#x25BE;</span>;
  return (
    <span className="text-accent ml-1">
      {sortDirection === "asc" ? "\u25B4" : "\u25BE"}
    </span>
  );
}

export default function DataTable({
  properties,
  sortField,
  sortDirection,
  onSort,
  page,
  pageSize,
  onPageChange,
}: DataTableProps) {
  const totalPages = Math.ceil(properties.length / pageSize);
  const startIdx = page * pageSize;
  const visible = properties.slice(startIdx, startIdx + pageSize);

  return (
    <div className="flex flex-col gap-3">
      {/* Table */}
      <div className="bg-card-bg border border-card-border rounded-xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="deal-table w-full text-sm">
            <thead>
              <tr className="border-b border-card-border">
                <th className="px-3 py-3 text-left text-xs font-medium text-muted uppercase tracking-wide w-8">
                  #
                </th>
                {columns.map((col) => (
                  <th
                    key={col.key}
                    className={`px-3 py-3 text-xs font-medium text-muted uppercase tracking-wide cursor-pointer hover:text-foreground transition-colors select-none ${
                      col.align === "right"
                        ? "text-right"
                        : col.align === "center"
                          ? "text-center"
                          : "text-left"
                    }`}
                    style={col.minWidth ? { minWidth: col.minWidth } : undefined}
                    onClick={() => onSort(col.key)}
                  >
                    <span className="inline-flex items-center">
                      {col.label}
                      <SortIcon
                        field={col.key}
                        sortField={sortField}
                        sortDirection={sortDirection}
                      />
                    </span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {visible.map((property, idx) => (
                <tr
                  key={`${property.address}-${property.block_lot}-${startIdx + idx}`}
                  className="border-b border-card-border/50 hover:bg-table-row-hover transition-colors"
                >
                  <td className="px-3 py-2.5 text-xs text-muted font-mono">
                    {startIdx + idx + 1}
                  </td>
                  {columns.map((col) => (
                    <td
                      key={col.key}
                      className={`px-3 py-2.5 ${
                        col.align === "right"
                          ? "text-right"
                          : col.align === "center"
                            ? "text-center"
                            : "text-left"
                      }`}
                    >
                      {col.render(property)}
                    </td>
                  ))}
                </tr>
              ))}
              {visible.length === 0 && (
                <tr>
                  <td
                    colSpan={columns.length + 1}
                    className="px-3 py-12 text-center text-muted"
                  >
                    No properties match the current filters.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Pagination */}
      <div className="flex items-center justify-between text-sm">
        <span className="text-muted">
          Showing {properties.length > 0 ? startIdx + 1 : 0}--
          {Math.min(startIdx + pageSize, properties.length)} of{" "}
          {properties.length.toLocaleString()} properties
        </span>
        <div className="flex items-center gap-2">
          <button
            onClick={() => onPageChange(0)}
            disabled={page === 0}
            className="px-3 py-1.5 rounded-lg bg-input-bg border border-input-border text-foreground hover:bg-table-row-hover disabled:opacity-30 disabled:cursor-not-allowed transition-colors cursor-pointer"
          >
            First
          </button>
          <button
            onClick={() => onPageChange(page - 1)}
            disabled={page === 0}
            className="px-3 py-1.5 rounded-lg bg-input-bg border border-input-border text-foreground hover:bg-table-row-hover disabled:opacity-30 disabled:cursor-not-allowed transition-colors cursor-pointer"
          >
            Prev
          </button>
          <span className="px-3 py-1.5 text-muted font-mono">
            {page + 1} / {totalPages || 1}
          </span>
          <button
            onClick={() => onPageChange(page + 1)}
            disabled={page >= totalPages - 1}
            className="px-3 py-1.5 rounded-lg bg-input-bg border border-input-border text-foreground hover:bg-table-row-hover disabled:opacity-30 disabled:cursor-not-allowed transition-colors cursor-pointer"
          >
            Next
          </button>
          <button
            onClick={() => onPageChange(totalPages - 1)}
            disabled={page >= totalPages - 1}
            className="px-3 py-1.5 rounded-lg bg-input-bg border border-input-border text-foreground hover:bg-table-row-hover disabled:opacity-30 disabled:cursor-not-allowed transition-colors cursor-pointer"
          >
            Last
          </button>
        </div>
      </div>
    </div>
  );
}
