// Scalar is bundled locally at the pinned package version. All reference data
// comes from the versioned OpenAPI YAML served by this same service.
import { createApiReference } from '@scalar/api-reference';

createApiReference('#scalar-reference', {
  url: '/openapi.yaml',
  theme: 'kepler',
  darkMode: true,
  layout: 'modern',
  showSidebar: true,
  withDefaultFonts: false,
  customCss: `
    .scalar-app { --scalar-font: system-ui, sans-serif; }
    .scalar-api-reference { max-width: 100%; }
  `,
});
