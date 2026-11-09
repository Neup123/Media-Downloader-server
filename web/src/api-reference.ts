// Scalar is bundled locally at the pinned package version. All reference data
// comes from the versioned OpenAPI YAML served by this same service.
import { createApiReference } from '@scalar/api-reference';
// The package stylesheet is required; otherwise all endpoint controls render unstyled.
import '@scalar/api-reference/style.css';

createApiReference('#scalar-reference', {
  url: '/openapi.yaml',
  theme: 'kepler',
  darkMode: true,
  layout: 'modern',
  showSidebar: true,
  withDefaultFonts: false,
  customCss: `
    .scalar-app { --scalar-font: system-ui, sans-serif; --scalar-background-1: #10151e; --scalar-background-2: #18212e; --scalar-background-3: #25374e; --scalar-color-1: #e8edf5; --scalar-color-2: #c1cbdb; --scalar-color-accent: #94bbff; }
    .scalar-api-reference { max-width: 100%; min-width: 0; }
  `,
});
