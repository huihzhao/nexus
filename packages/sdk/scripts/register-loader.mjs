/**
 * Register the custom ESM loader for fixing @bnb-chain import paths.
 * Use with: node --import ./scripts/register-loader.mjs <script>
 */
import { register } from "node:module";
import { pathToFileURL } from "node:url";

register("./esm-loader.mjs", pathToFileURL(import.meta.url));
