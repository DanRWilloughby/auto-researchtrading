import Link from "next/link";

const stats = [
  { label: "Properties Scored", value: "20,551", sub: "daily automated scan" },
  { label: "Median Cap Rate", value: "7.5%", sub: "as-is, before renovation" },
  { label: "Active Sheriff Sales", value: "27", sub: "16 matched to database" },
  { label: "Absentee-Owned", value: "75%", sub: "914 out-of-state" },
  { label: "Opportunity Zone", value: "8,085", sub: "39% of scored properties" },
  { label: "High Distress", value: "6,168", sub: "score 3+ of 10" },
];

const dataSources = [
  { name: "NJ MOD-IV", desc: "Every parcel: assessed value, owner, sale history, units", freq: "Weekly" },
  { name: "HUD Fair Market Rents", desc: "Rent benchmarks by ZIP code and bedroom count", freq: "Annual" },
  { name: "Zillow ZORI", desc: "Observed market rents by ZIP, monthly time series", freq: "Monthly" },
  { name: "Census ACS", desc: "Vacancy, income, population by census tract", freq: "Annual" },
  { name: "CivilView Sheriff Sales", desc: "Active foreclosures with upset prices and case details", freq: "Weekly" },
  { name: "FEMA Flood Maps", desc: "Flood zone designations for risk scoring", freq: "Static" },
  { name: "HUD Opportunity Zones", desc: "Tax-advantaged census tracts", freq: "Static" },
];

const capabilities = [
  {
    title: "Property Scoring Engine",
    desc: "Every property scored across 5 dimensions: return potential, value-add spread, acquisition likelihood, risk factors, and tax advantages. Updated daily.",
    icon: (
      <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 013 19.875v-6.75zM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V8.625zM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V4.125z" />
      </svg>
    ),
  },
  {
    title: "Distressed Deal Monitoring",
    desc: "Sheriff sales, absentee owners, tax delinquency, code violations. The platform identifies motivated sellers before they list.",
    icon: (
      <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M14.857 17.082a23.848 23.848 0 005.454-1.31A8.967 8.967 0 0118 9.75v-.7V9A6 6 0 006 9v.75a8.967 8.967 0 01-2.312 6.022c1.733.64 3.56 1.085 5.455 1.31m5.714 0a24.255 24.255 0 01-5.714 0m5.714 0a3 3 0 11-5.714 0" />
      </svg>
    ),
  },
  {
    title: "Comparable Sales Analysis",
    desc: "For any target property, auto-find the 10 closest comparable recent sales. Comp-implied valuation range with confidence level.",
    icon: (
      <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7.5 14.25v2.25m3-4.5v4.5m3-6.75v6.75m3-9v9M6 20.25h12A2.25 2.25 0 0020.25 18V6A2.25 2.25 0 0018 3.75H6A2.25 2.25 0 003.75 6v12A2.25 2.25 0 006 20.25z" />
      </svg>
    ),
  },
  {
    title: "Portfolio Optimizer",
    desc: "Given a fund size, compute the optimal property mix. Maximize blended return while diversifying across ZIP codes and property classes.",
    icon: (
      <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M2.25 18.75a60.07 60.07 0 0115.797 2.101c.727.198 1.453-.342 1.453-1.096V18.75M3.75 4.5v.75A.75.75 0 013 6h-.75m0 0v-.375c0-.621.504-1.125 1.125-1.125H20.25M2.25 6v9m18-10.5v.75c0 .414.336.75.75.75h.75m-1.5-1.5h.375c.621 0 1.125.504 1.125 1.125v9.75c0 .621-.504 1.125-1.125 1.125h-.375m1.5-1.5H21a.75.75 0 00-.75.75v.75m0 0H3.75m0 0h-.375a1.125 1.125 0 01-1.125-1.125V15m1.5 1.5v-.75A.75.75 0 003 15h-.75M15 10.5a3 3 0 11-6 0 3 3 0 016 0zm3 0h.008v.008H18V10.5zm-12 0h.008v.008H6V10.5z" />
      </svg>
    ),
  },
  {
    title: "Direct Mail Targeting",
    desc: "Mail merge-ready lists of out-of-state and absentee owners sorted by distress score. Actual mailing addresses for direct outreach.",
    icon: (
      <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M21.75 6.75v10.5a2.25 2.25 0 01-2.25 2.25h-15a2.25 2.25 0 01-2.25-2.25V6.75m19.5 0A2.25 2.25 0 0019.5 4.5h-15a2.25 2.25 0 00-2.25 2.25m19.5 0v.243a2.25 2.25 0 01-1.07 1.916l-7.5 4.615a2.25 2.25 0 01-2.36 0L3.32 8.91a2.25 2.25 0 01-1.07-1.916V6.75" />
      </svg>
    ),
  },
  {
    title: "Backtested & Validated",
    desc: "Thesis validated against 4,461 historical transactions. Stress tested across 10 adverse scenarios. Model breaks even at 20% rent decline.",
    icon: (
      <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" />
      </svg>
    ),
  },
];

const sampleDeal = {
  address: "307 Van Houten Street",
  type: "3-unit Residential",
  status: "Sheriff Sale — April 7, 2026",
  upset: "$316,915",
  compMedian: "$525,000",
  noiStabilized: "$55,650/yr",
  equityMultiple: "2.25x",
  rating: "BUY",
  score: 0.797,
};

export default function Home() {
  return (
    <div className="min-h-screen">
      {/* Nav */}
      <nav className="border-b border-card-border bg-card-bg/80 backdrop-blur-sm sticky top-0 z-50">
        <div className="max-w-6xl mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 bg-accent rounded-lg flex items-center justify-center">
              <span className="text-white font-bold text-sm">F</span>
            </div>
            <span className="font-semibold text-foreground tracking-tight">Forte Deal Intelligence</span>
          </div>
          <div className="flex items-center gap-4">
            <Link href="/dashboard" className="text-sm text-accent hover:text-foreground transition-colors">
              Dashboard
            </Link>
            <Link
              href="/dashboard"
              className="text-sm bg-accent hover:bg-accent/80 text-white px-4 py-2 rounded-lg transition-colors"
            >
              Open Platform
            </Link>
          </div>
        </div>
      </nav>

      {/* Hero */}
      <section className="relative overflow-hidden">
        <div className="absolute inset-0 bg-gradient-to-b from-accent/5 to-transparent pointer-events-none" />
        <div className="max-w-6xl mx-auto px-6 pt-20 pb-16">
          <div className="max-w-3xl">
            <div className="inline-flex items-center gap-2 text-xs font-medium text-accent bg-accent/10 border border-accent/20 rounded-full px-3 py-1 mb-6">
              <span className="w-1.5 h-1.5 bg-success rounded-full animate-pulse" />
              Live — updated daily at 7:00 AM ET
            </div>
            <h1 className="text-4xl md:text-5xl font-bold text-foreground tracking-tight leading-tight">
              Paterson Deal
              <br />
              Intelligence Platform
            </h1>
            <p className="mt-6 text-lg text-muted leading-relaxed max-w-2xl">
              Automated research that continuously scans{" "}
              <span className="text-foreground font-medium">every property in Paterson, NJ</span> and
              identifies the highest-return value-add investment opportunities.
              Powered by 7 public data sources, backtested against 4,461 historical transactions.
            </p>
            <div className="mt-8 flex flex-wrap gap-4">
              <Link
                href="/dashboard"
                className="inline-flex items-center gap-2 bg-accent hover:bg-accent/80 text-white font-medium px-6 py-3 rounded-lg transition-colors"
              >
                Explore Properties
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 7l5 5m0 0l-5 5m5-5H6" />
                </svg>
              </Link>
              <a
                href="#how-it-works"
                className="inline-flex items-center gap-2 border border-card-border hover:border-muted text-foreground font-medium px-6 py-3 rounded-lg transition-colors"
              >
                How It Works
              </a>
            </div>
          </div>
        </div>
      </section>

      {/* Stats */}
      <section className="border-y border-card-border bg-card-bg/50">
        <div className="max-w-6xl mx-auto px-6 py-10">
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-6">
            {stats.map((s) => (
              <div key={s.label}>
                <div className="text-2xl md:text-3xl font-bold text-foreground">{s.value}</div>
                <div className="text-sm font-medium text-accent mt-1">{s.label}</div>
                <div className="text-xs text-muted mt-0.5">{s.sub}</div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* What it does */}
      <section id="how-it-works" className="py-20">
        <div className="max-w-6xl mx-auto px-6">
          <div className="text-center mb-14">
            <h2 className="text-3xl font-bold text-foreground">What the Platform Does</h2>
            <p className="text-muted mt-3 max-w-xl mx-auto">
              Institutional-grade deal sourcing that replaces gut feel with quantified, ranked opportunities.
            </p>
          </div>
          <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6">
            {capabilities.map((c) => (
              <div
                key={c.title}
                className="border border-card-border rounded-xl p-6 bg-card-bg hover:border-accent/30 transition-colors"
              >
                <div className="w-10 h-10 rounded-lg bg-accent/10 text-accent flex items-center justify-center mb-4">
                  {c.icon}
                </div>
                <h3 className="text-lg font-semibold text-foreground">{c.title}</h3>
                <p className="text-sm text-muted mt-2 leading-relaxed">{c.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Sample Deal */}
      <section className="py-20 border-t border-card-border">
        <div className="max-w-6xl mx-auto px-6">
          <div className="text-center mb-14">
            <h2 className="text-3xl font-bold text-foreground">Sample Deal Analysis</h2>
            <p className="text-muted mt-3 max-w-xl mx-auto">
              Every property in the pipeline gets this level of analysis — automatically.
            </p>
          </div>
          <div className="max-w-2xl mx-auto border border-card-border rounded-xl bg-card-bg overflow-hidden">
            <div className="bg-danger/10 border-b border-card-border px-6 py-3 flex items-center gap-2">
              <span className="w-2 h-2 bg-danger rounded-full animate-pulse" />
              <span className="text-sm font-medium text-danger">Sheriff Sale — Immediate Opportunity</span>
            </div>
            <div className="p-6">
              <h3 className="text-xl font-bold text-foreground">{sampleDeal.address}</h3>
              <p className="text-sm text-muted mt-1">{sampleDeal.type} &middot; {sampleDeal.status}</p>

              <div className="grid grid-cols-2 gap-4 mt-6">
                <div className="border border-card-border rounded-lg p-4">
                  <div className="text-xs text-muted uppercase tracking-wide">Upset Price</div>
                  <div className="text-lg font-bold text-foreground mt-1">{sampleDeal.upset}</div>
                </div>
                <div className="border border-card-border rounded-lg p-4">
                  <div className="text-xs text-muted uppercase tracking-wide">Comp Median</div>
                  <div className="text-lg font-bold text-success mt-1">{sampleDeal.compMedian}</div>
                </div>
                <div className="border border-card-border rounded-lg p-4">
                  <div className="text-xs text-muted uppercase tracking-wide">NOI (Stabilized)</div>
                  <div className="text-lg font-bold text-foreground mt-1">{sampleDeal.noiStabilized}</div>
                </div>
                <div className="border border-card-border rounded-lg p-4">
                  <div className="text-xs text-muted uppercase tracking-wide">Equity Multiple</div>
                  <div className="text-lg font-bold text-accent mt-1">{sampleDeal.equityMultiple}</div>
                </div>
              </div>

              <div className="mt-6 flex items-center justify-between">
                <div>
                  <span className="text-xs text-muted">Composite Score</span>
                  <div className="flex items-center gap-3 mt-1">
                    <div className="w-48 h-2 bg-card-border rounded-full overflow-hidden">
                      <div
                        className="h-full bg-accent rounded-full"
                        style={{ width: `${sampleDeal.score * 100}%` }}
                      />
                    </div>
                    <span className="text-sm font-mono text-foreground">{sampleDeal.score.toFixed(3)}</span>
                  </div>
                </div>
                <span className="text-sm font-bold text-success bg-success/10 px-3 py-1 rounded-full">
                  {sampleDeal.rating}
                </span>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Investment Thesis */}
      <section className="py-20 border-t border-card-border bg-card-bg/30">
        <div className="max-w-6xl mx-auto px-6">
          <div className="text-center mb-14">
            <h2 className="text-3xl font-bold text-foreground">The Thesis — Validated</h2>
            <p className="text-muted mt-3 max-w-2xl mx-auto">
              The platform backtested against 4,461 historical transactions. The value-add spread is real,
              and it&apos;s operational alpha — not market timing.
            </p>
          </div>
          <div className="grid md:grid-cols-3 gap-6 max-w-4xl mx-auto">
            <div className="border border-card-border rounded-xl p-6 bg-card-bg text-center">
              <div className="text-4xl font-bold text-accent">1.1x</div>
              <div className="text-sm font-medium text-foreground mt-2">Buy at Assessed</div>
              <p className="text-xs text-muted mt-2">Off-market deals transact at ~1.05x assessed value. The platform identifies the most motivated sellers.</p>
            </div>
            <div className="border border-card-border rounded-xl p-6 bg-card-bg text-center">
              <div className="text-4xl font-bold text-success">+20%</div>
              <div className="text-sm font-medium text-foreground mt-2">Renovation Rent Lift</div>
              <p className="text-xs text-muted mt-2">Renovated units command a 20% rent premium. Market rents: $2,000/2BR. Renovated: $2,400/2BR.</p>
            </div>
            <div className="border border-card-border rounded-xl p-6 bg-card-bg text-center">
              <div className="text-4xl font-bold text-warning">2.5x</div>
              <div className="text-sm font-medium text-foreground mt-2">Exit at Market</div>
              <p className="text-xs text-muted mt-2">Stabilized multifamily sells at ~2.5x assessed value on the open market. The spread is where returns live.</p>
            </div>
          </div>
          <div className="mt-10 max-w-2xl mx-auto border border-card-border rounded-xl p-6 bg-card-bg">
            <h3 className="text-sm font-semibold text-foreground mb-4">Stress Test Results</h3>
            <div className="space-y-3 text-sm">
              {[
                { scenario: "Base case", equity: "1.72x", yield: "11.2%", status: "pass" },
                { scenario: "Rent decline 20%", equity: "1.25x", yield: "8.2%", status: "pass" },
                { scenario: "Reno cost +50%", equity: "1.52x", yield: "9.9%", status: "pass" },
                { scenario: "Cap rate expansion to 9%", equity: "1.24x", yield: "11.2%", status: "pass" },
                { scenario: "Tax reassessment +50%", equity: "1.44x", yield: "9.4%", status: "pass" },
                { scenario: "Worst case combo", equity: "0.78x", yield: "6.6%", status: "fail" },
              ].map((row) => (
                <div key={row.scenario} className="flex items-center justify-between py-2 border-b border-card-border last:border-0">
                  <span className="text-muted">{row.scenario}</span>
                  <div className="flex items-center gap-4">
                    <span className="font-mono text-foreground">{row.equity}</span>
                    <span className="font-mono text-muted">{row.yield}</span>
                    <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${
                      row.status === "pass" ? "bg-success/10 text-success" : "bg-danger/10 text-danger"
                    }`}>
                      {row.status === "pass" ? "PASS" : "LOSS"}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* Data Sources */}
      <section className="py-20 border-t border-card-border">
        <div className="max-w-6xl mx-auto px-6">
          <div className="text-center mb-14">
            <h2 className="text-3xl font-bold text-foreground">Data Sources</h2>
            <p className="text-muted mt-3 max-w-xl mx-auto">
              All public. All free. All automated.
            </p>
          </div>
          <div className="max-w-3xl mx-auto">
            <div className="border border-card-border rounded-xl overflow-hidden">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-card-bg border-b border-card-border">
                    <th className="text-left px-5 py-3 font-medium text-muted">Source</th>
                    <th className="text-left px-5 py-3 font-medium text-muted">Provides</th>
                    <th className="text-left px-5 py-3 font-medium text-muted">Updated</th>
                  </tr>
                </thead>
                <tbody>
                  {dataSources.map((d) => (
                    <tr key={d.name} className="border-b border-card-border last:border-0 hover:bg-table-row-hover transition-colors">
                      <td className="px-5 py-3 font-medium text-foreground whitespace-nowrap">{d.name}</td>
                      <td className="px-5 py-3 text-muted">{d.desc}</td>
                      <td className="px-5 py-3 text-muted whitespace-nowrap">{d.freq}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </section>

      {/* Why Forte */}
      <section className="py-20 border-t border-card-border bg-card-bg/30">
        <div className="max-w-6xl mx-auto px-6">
          <div className="text-center mb-14">
            <h2 className="text-3xl font-bold text-foreground">Built for Forte</h2>
            <p className="text-muted mt-3 max-w-2xl mx-auto">
              This platform solves the specific challenges of running a Reg A real estate fund.
            </p>
          </div>
          <div className="grid md:grid-cols-2 gap-8 max-w-4xl mx-auto">
            {[
              {
                title: "Investor Confidence",
                desc: "Retail investors at $1,000 minimums need to trust the process. \"We quantitatively score every property in the market\" is a much stronger pitch than \"our team knows the neighborhood.\"",
              },
              {
                title: "Deal Flow Generation",
                desc: "The direct mail module provides mailing addresses for 200+ out-of-state distressed property owners. Veloce can begin outreach next week.",
              },
              {
                title: "Defensible Returns",
                desc: "When you tell investors \"we target 1.7x equity multiples,\" this platform provides the backtest data, comp analysis, and stress tests behind that number.",
              },
              {
                title: "SEC-Ready Process",
                desc: "Reg A Tier II requires demonstrating sound methodology. A data-driven, backtested platform with documented sources is significantly stronger than qualitative deal selection.",
              },
            ].map((item) => (
              <div key={item.title} className="flex gap-4">
                <div className="w-1 bg-accent rounded-full flex-shrink-0" />
                <div>
                  <h3 className="font-semibold text-foreground">{item.title}</h3>
                  <p className="text-sm text-muted mt-2 leading-relaxed">{item.desc}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="py-20 border-t border-card-border">
        <div className="max-w-6xl mx-auto px-6 text-center">
          <h2 className="text-3xl font-bold text-foreground">Explore the Data</h2>
          <p className="text-muted mt-3 max-w-lg mx-auto">
            Browse all 20,551 scored properties. Filter by property class, units, cap rate, distress signals, and more.
          </p>
          <Link
            href="/dashboard"
            className="mt-8 inline-flex items-center gap-2 bg-accent hover:bg-accent/80 text-white font-medium px-8 py-4 rounded-xl text-lg transition-colors"
          >
            Open the Dashboard
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 7l5 5m0 0l-5 5m5-5H6" />
            </svg>
          </Link>
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t border-card-border bg-card-bg/50 py-8">
        <div className="max-w-6xl mx-auto px-6 flex flex-col md:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="w-6 h-6 bg-accent rounded flex items-center justify-center">
              <span className="text-white font-bold text-xs">F</span>
            </div>
            <span className="text-sm text-muted">Forte Deal Intelligence Platform</span>
          </div>
          <div className="text-xs text-muted">
            Data as of March 29, 2026 &middot; Paterson, NJ &middot; 7 public data sources &middot; Updated daily
          </div>
        </div>
      </footer>
    </div>
  );
}
