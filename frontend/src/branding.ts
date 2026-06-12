// App branding. The icon is configurable via frontend/.env so deployments can
// swap the mark without touching code:
//
//   VITE_APP_ICON=/nexus_icon.png        (default — classic mark)
//   VITE_APP_ICON=/nexus_icon_neon.png   (neon cloud+bolt mascot)
//
// Any path under frontend/public/ (or an absolute URL allowed by the CSP)
// works. The favicon in index.html uses the same variable via Vite's
// %VITE_APP_ICON% HTML substitution.

export const APP_ICON: string =
  import.meta.env.VITE_APP_ICON || "/nexus_icon.png";
