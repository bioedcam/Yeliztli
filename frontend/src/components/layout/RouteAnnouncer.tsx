/** Announces route changes to screen readers (P4-26c WCAG 2.1 AA).
 *
 * Uses an aria-live region to notify assistive technology when the
 * page title changes after a client-side navigation.
 */

import { useLocation } from "react-router-dom"

/** Map pathname to a human-readable page title for screen reader announcements. */
function getPageTitle(pathname: string): string {
  const routes: Record<string, string> = {
    "/": "Dashboard",
    "/findings": "All Findings",
    "/variants": "Variant Explorer",
    "/pharmacogenomics": "Pharmacogenomics",
    "/nutrigenomics": "Nutrigenomics",
    "/cancer": "Cancer",
    "/cardiovascular": "Cardiovascular",
    "/apoe": "APOE",
    "/carrier-status": "Carrier Status",
    "/fitness": "Gene Fitness",
    "/sleep": "Gene Sleep",
    "/methylation": "MTHFR & Methylation",
    "/skin": "Gene Skin",
    "/allergy": "Gene Allergy & Immune Sensitivities",
    "/traits": "Traits & Personality",
    "/gene-health": "Gene Health",
    "/ancestry": "Ancestry",
    "/rare-variants": "Rare Variants",
    "/genome-browser": "Genome Browser",
    "/query-builder": "Query Builder",
    "/overlays": "Overlays",
    "/reports": "Reports",
    "/settings": "Settings",
    "/setup": "Setup Wizard",
    "/login": "Login",
  }

  // Check exact match first, then try parent path for nested routes (e.g. /settings/updates → Settings)
  if (routes[pathname]) return routes[pathname]
  const parent = pathname.split("/").slice(0, 2).join("/")
  return routes[parent] ?? "Page"
}

export default function RouteAnnouncer() {
  const location = useLocation()
  const title = getPageTitle(location.pathname)

  return (
    <div
      data-testid="route-announcer"
      role="status"
      aria-live="polite"
      aria-atomic="true"
      className="sr-only"
    >
      {`Navigated to ${title}`}
    </div>
  )
}
