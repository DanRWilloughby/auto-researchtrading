import { loadProperties } from "../lib/load-data";
import Dashboard from "../components/Dashboard";

export default function DashboardPage() {
  const properties = loadProperties();
  const dataDate = "March 29, 2026";

  return <Dashboard properties={properties} dataDate={dataDate} />;
}
