/**
 * Custom ESM loader to fix missing .js extensions in @bnb-chain packages.
 * Node.js v22+ enforces strict ESM resolution but some packages omit .js.
 *
 * Usage: node --import ./scripts/register-loader.mjs scripts/create_greenfield_bucket.mjs
 */

export async function resolve(specifier, context, nextResolve) {
  try {
    return await nextResolve(specifier, context);
  } catch (err) {
    // If module not found and no .js extension, try adding .js
    if (err.code === "ERR_MODULE_NOT_FOUND" && !specifier.endsWith(".js") && !specifier.endsWith(".mjs")) {
      try {
        return await nextResolve(specifier + ".js", context);
      } catch {
        // Also try /index.js
        try {
          return await nextResolve(specifier + "/index.js", context);
        } catch {
          throw err; // throw original error
        }
      }
    }
    throw err;
  }
}
