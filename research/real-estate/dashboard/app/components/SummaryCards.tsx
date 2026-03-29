"use client";

import { Property } from "../types";

interface SummaryCardsProps {
  properties: Property[];
  filteredCount: number;
}

function median(values: number[]): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 !== 0
    ? sorted[mid]
    : (sorted[mid - 1] + sorted[mid]) / 2;
}

export default function SummaryCards({
  properties,
  filteredCount,
}: SummaryCardsProps) {
  const totalProperties = properties.length;
  const capRates = properties
    .map((p) => p.cap_rate_current)
    .filter((v): v is number => v !== null && v > 0);
  const medianCapRate = median(capRates);
  const sheriffSaleCount = properties.filter((p) => p.in_sheriff_sale).length;
  const highDistressCount = properties.filter(
    (p) => p.distress_score !== null && p.distress_score >= 4
  ).length;
  const avgComposite =
    properties.reduce((sum, p) => sum + (p.composite_score ?? 0), 0) /
    (totalProperties || 1);
  const absenteeCount = properties.filter(
    (p) =>
      p.absentee_status &&
      p.absentee_status !== "" &&
      p.absentee_status !== "owner_occupied"
  ).length;

  const cards = [
    {
      label: "Properties Shown",
      value: filteredCount.toLocaleString(),
      sub: `of ${totalProperties.toLocaleString()} total`,
      color: "text-accent",
    },
    {
      label: "Median Cap Rate",
      value: `${(medianCapRate * 100).toFixed(1)}%`,
      sub: `from ${capRates.length.toLocaleString()} properties`,
      color: "text-success",
    },
    {
      label: "Sheriff Sales",
      value: sheriffSaleCount.toLocaleString(),
      sub: `${((sheriffSaleCount / totalProperties) * 100).toFixed(1)}% of total`,
      color: "text-danger",
    },
    {
      label: "High Distress (4+)",
      value: highDistressCount.toLocaleString(),
      sub: `${((highDistressCount / totalProperties) * 100).toFixed(1)}% of total`,
      color: "text-warning",
    },
    {
      label: "Absentee Owners",
      value: absenteeCount.toLocaleString(),
      sub: `${((absenteeCount / totalProperties) * 100).toFixed(1)}% of total`,
      color: "text-foreground",
    },
    {
      label: "Avg Composite Score",
      value: avgComposite.toFixed(4),
      sub: "higher = better deal",
      color: "text-accent",
    },
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
      {cards.map((card) => (
        <div
          key={card.label}
          className="bg-card-bg border border-card-border rounded-xl p-4 flex flex-col gap-1"
        >
          <span className="text-xs font-medium text-muted uppercase tracking-wide">
            {card.label}
          </span>
          <span className={`text-2xl font-bold ${card.color} font-mono`}>
            {card.value}
          </span>
          <span className="text-xs text-muted">{card.sub}</span>
        </div>
      ))}
    </div>
  );
}
