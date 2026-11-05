// Bundle Scalar with the standalone UI; loading the API reference must never
// depend on a CDN or on Nextcloud. The YAML contract stays on this origin.
import { createApiReference } from '@scalar/api-reference';

createApiReference('#scalar-reference', {
  url: '/openapi.yaml',
});
