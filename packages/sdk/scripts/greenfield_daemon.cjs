#!/usr/bin/env node
/**
 * Greenfield persistent daemon — stays alive, accepts commands via stdin.
 *
 * Protocol: one JSON line per command on stdin, one JSON line per response on stdout.
 *
 * Input:  {"id": 1, "op": "get", "object": "rune/abc123"}
 *         {"id": 2, "op": "put", "object": "rune/def456", "hex": "48656c6c6f"}
 *         {"id": 3, "op": "head", "object": "rune/abc123"}
 *         {"id": 4, "op": "list", "prefix": "rune/agents/"}
 *
 * Output: {"id": 1, "ok": true, "data_hex": "...", "size": 42}
 *         {"id": 2, "ok": true, "hash": "rune/def456", "size": 5}
 *         ...
 *
 * Stays alive until stdin closes or "exit" command.
 * Initializes SDK + SP list ONCE at startup.
 */

const { readFileSync, createReadStream } = require("fs");
const { resolve } = require("path");
const { createInterface } = require("readline");

// Load .env
function loadEnv() {
  for (const p of [resolve(process.cwd(), ".env"), resolve(__dirname, "..", ".env")]) {
    try {
      for (const line of readFileSync(p, "utf-8").split("\n")) {
        const t = line.trim();
        if (!t || t.startsWith("#")) continue;
        const eq = t.indexOf("=");
        if (eq === -1) continue;
        const k = t.slice(0, eq).trim();
        if (!process.env[k]) process.env[k] = t.slice(eq + 1).trim();
      }
      return;
    } catch (e) { /* next */ }
  }
}
loadEnv();

function out(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

const PRIVATE_KEY = process.env.NEXUS_PRIVATE_KEY;
const BUCKET = process.env.NEXUS_GREENFIELD_BUCKET || "nexus-agent-state";
const NETWORK = process.env.NEXUS_GREENFIELD_NETWORK || "testnet";

const CONFIGS = {
  testnet: { chainId: "5600", chainRpc: "https://gnfd-testnet-fullnode-tendermint-us.bnbchain.org" },
  mainnet: { chainId: "1017", chainRpc: "https://greenfield-chain.bnbchain.org" },
};

// ── Global state (initialized once) ──
let client = null;
let sdk = null;
let Long = null;
let wallet = null;
let address = null;
let pk = null;
let spAddr = null;
let spEndpoint = null;
let secondarySpAddresses = [];
let authType = null;
let ready = false;
// Bucket's primary SP — must be resolved before first put. Different
// from the chain-wide "first available SP" we pick at boot. If the
// daemon talks to the wrong SP for a bucket, every put returns the
// classic "Query failed with (6): No such bucket: unknown request"
// because that SP simply doesn't host the bucket.
let bucketPrimarySpAddr = null;
let bucketPrimarySpEndpoint = null;
let bucketResolved = false;

async function init() {
  if (!PRIVATE_KEY) {
    out({ id: 0, ok: false, error: "NEXUS_PRIVATE_KEY not set" });
    process.exit(1);
  }

  sdk = require("@bnb-chain/greenfield-js-sdk");
  const { ethers } = require("ethers");
  Long = sdk.Long || require("long");

  const config = CONFIGS[NETWORK] || CONFIGS.testnet;
  client = sdk.Client.create(config.chainRpc, String(config.chainId));

  wallet = new ethers.Wallet(PRIVATE_KEY);
  address = wallet.address;
  pk = PRIVATE_KEY.startsWith("0x") ? PRIVATE_KEY : `0x${PRIVATE_KEY}`;
  authType = { type: "ECDSA", privateKey: pk };

  // Get primary SP (cached for lifetime).
  //
  // Old rev: `spArray.find(IN_SERVICE) || spArray[0]` — i.e. blindly
  // trust chain's IN_SERVICE flag. Production showed that flag stays
  // True for SPs whose process has crashed (chain has no liveness
  // detection), so the daemon would pin its DEFAULT endpoint to a
  // dead SP and every fallback in resolveBucketSp() would route to it
  // → all PUTs ECONNREFUSED. We now probe each candidate's /status
  // endpoint with a 5s timeout and pick the first live one.
  const spListRes = await client.sp.getStorageProviders();
  const spArray = Array.isArray(spListRes) ? spListRes : (spListRes.sps || []);
  const inService = spArray.filter(
    sp => sp.status === 0 || sp.status === "STATUS_IN_SERVICE",
  );

  async function probeSpAlive(sp, timeoutMs) {
    const ep = sp.endpoint || sp.Endpoint;
    if (!ep) return false;
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const r = await fetch(`${ep.replace(/\/$/, "")}/status`,
                            { method: "GET", signal: ctrl.signal });
      return r.status < 500;
    } catch (_e) {
      return false;
    } finally {
      clearTimeout(timer);
    }
  }

  let primarySP = null;
  for (const sp of inService) {
    if (await probeSpAlive(sp, 5000)) { primarySP = sp; break; }
  }
  if (!primarySP) {
    // Worst case — every IN_SERVICE SP failed the probe. Still pick
    // SOMETHING so the daemon can boot, but log loud. Fallback to
    // chain order.
    primarySP = inService[0] || spArray[0];
    out({ id: 0, ok: true, info: "no-sp-probe-passed",
          warn: "every IN_SERVICE SP failed liveness probe — boot may be unstable" });
  }
  if (!primarySP) {
    out({ id: 0, ok: false, error: "No storage providers found" });
    process.exit(1);
  }
  spAddr = primarySP.operatorAddress || primarySP.operator_address;
  spEndpoint = primarySP.endpoint;
  secondarySpAddresses = spArray
    .filter(sp => {
      const addr = sp.operatorAddress || sp.operator_address;
      return addr && addr !== spAddr;
    })
    .map(sp => sp.operatorAddress || sp.operator_address);

  ready = true;
  out({ id: 0, ok: true, status: "ready", sps: spArray.length, address, bucket: BUCKET, network: NETWORK });
}

// ── Operations (reuse initialized client) ──

/**
 * Resolve the bucket's PRIMARY SP and pin it as the default endpoint
 * for object operations. In Greenfield each bucket lives on a
 * specific primary SP; uploads MUST go to that SP. Old code relied
 * on the JS SDK auto-selecting "first available SP" which produces
 * the canonical "Query failed with (6): No such bucket" when the
 * picked SP doesn't host the bucket. Idempotent — runs once.
 */
async function resolveBucketSp() {
  if (bucketResolved) return true;

  const config = CONFIGS[NETWORK] || CONFIGS.testnet;
  const spListRes = await client.sp.getStorageProviders();
  const spArray = Array.isArray(spListRes) ? spListRes : (spListRes.sps || []);

  // ── Primary lookup path: chain LCD API (NOT the SDK).
  //
  // Why bypass the SDK: greenfield-js-sdk v2's getBucketMeta /
  // headBucket return shapes shifted across minor releases, and the
  // field that USED to be `primarySpId` got moved into a GVG family
  // wrapper. Our v1.x-era field-name fallback table was matching
  // `globalVirtualGroupFamilyId` as a "best effort" — but a GVG
  // family id is NOT a primary SP id, so SP lookup ALWAYS missed,
  // resolveBucketSp ALWAYS hit the chain-wide-first-SP fallback, and
  // every PUT routed to whichever SP happened to be at index 0 of
  // chain SP order (in production: 0x1Eb29..., a dead SP). Result:
  // bucket's real primary SP was healthy, but we never used it.
  //
  // The chain LCD `head_bucket` endpoint speaks the canonical
  // bucket_info shape and exposes `primary_sp_id` directly under
  // chain v1 semantics. For v2 GVG-only buckets we read
  // `global_virtual_group_family_id` and walk the family to its
  // primary SP id via `head_global_virtual_group_family`.
  //
  // If both LCD calls fail (network glitch, malformed response), we
  // fall through to "first probe-passing SP" rather than blindly
  // taking chain[0] — small chance of routing to a non-bucket SP,
  // but the upload will be rejected loud by Greenfield and chain.py
  // surfaces the failure to the desktop instead of going silent.
  let primarySpId = null;
  let resolvedVia = null;
  try {
    const url = `${config.chainRpc}/greenfield/storage/head_bucket/${BUCKET}`;
    const r = await fetch(url);
    if (r.ok) {
      const data = await r.json();
      const bi = data?.bucket_info || {};
      if (bi.primary_sp_id !== undefined && bi.primary_sp_id !== null) {
        primarySpId = bi.primary_sp_id;
        resolvedVia = "lcd-head-bucket";
      } else if (bi.global_virtual_group_family_id !== undefined) {
        // GVG-family buckets: family.primary_sp_id is the truth.
        const famUrl = `${config.chainRpc}/greenfield/virtualgroup/v1/global_virtual_group_family?family_id=${bi.global_virtual_group_family_id}`;
        const fr = await fetch(famUrl);
        if (fr.ok) {
          const fd = await fr.json();
          primarySpId =
            fd?.global_virtual_group_family?.primary_sp_id ?? null;
          if (primarySpId !== null) resolvedVia = "lcd-gvg-family";
        }
      }
    }
  } catch (_e) {
    // network / parse failure — leave primarySpId null and fall through.
  }

  let primarySP = null;
  if (primarySpId !== null) {
    primarySP = spArray.find(sp => {
      const idCandidates = [sp.id, sp.Id, sp.spId, sp.sp_id]
        .filter(v => v !== undefined && v !== null)
        .map(v => String(v));
      return idCandidates.includes(String(primarySpId));
    });
  }

  // ── Fallback path: probe each IN_SERVICE SP for liveness, take
  //    the first that responds. Crucially this is NOT chain[0] —
  //    that's how we ended up routing to a dead SP in production.
  async function probeSpAlive(sp, timeoutMs) {
    const ep = sp.endpoint || sp.Endpoint;
    if (!ep) return false;
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const r = await fetch(`${ep.replace(/\/$/, "")}/status`,
                            { method: "GET", signal: ctrl.signal });
      return r.status < 500;
    } catch (_e) {
      return false;
    } finally {
      clearTimeout(timer);
    }
  }

  if (!primarySP) {
    const inService = spArray.filter(
      sp => sp.status === 0 || sp.status === "STATUS_IN_SERVICE",
    );
    for (const sp of inService) {
      if (await probeSpAlive(sp, 5000)) { primarySP = sp; resolvedVia = "probe-fallback"; break; }
    }
  }

  if (primarySP) {
    bucketPrimarySpAddr = primarySP.operatorAddress || primarySP.operator_address;
    bucketPrimarySpEndpoint = primarySP.endpoint;
    out({ id: 0, ok: true, info: "bucket-sp-resolved",
          bucket: BUCKET, sp: bucketPrimarySpAddr,
          endpoint: bucketPrimarySpEndpoint, via: resolvedVia });
  } else {
    bucketPrimarySpAddr = spAddr;
    bucketPrimarySpEndpoint = spEndpoint;
    out({ id: 0, ok: true, info: "bucket-sp-fallback",
          bucket: BUCKET, sp: spAddr,
          reason: "no SP available — bucket may have been bound to a dead primary SP" });
  }
  bucketResolved = true;
  return primarySP !== null;
}

async function doPut(id, objectName, hexData) {
  const data = Buffer.from(hexData, "hex");
  await resolveBucketSp();

  // Check if object already exists
  try {
    const headRes = await client.object.headObject(BUCKET, objectName);
    if (headRes && headRes.objectInfo) {
      out({ id, ok: true, hash: objectName, size: data.length, existed: true });
      return;
    }
  } catch (e) { /* doesn't exist, proceed */ }

  const VISIBILITY_PRIVATE = sdk.VisibilityType
    ? sdk.VisibilityType.VISIBILITY_TYPE_PRIVATE
    : 2;

  try {
    const body = Buffer.from(data);
    body.size = body.length;
    body.type = "application/octet-stream";

    // Pass the resolved bucket primary SP endpoint explicitly so the
    // SDK doesn't auto-select a wrong one. Different SDK versions
    // name this option differently — try the common spellings.
    const opts = {
      bucketName: BUCKET,
      objectName: objectName,
      body: body,
      delegatedOpts: { visibility: VISIBILITY_PRIVATE },
      timeout: 60000,
    };
    if (bucketPrimarySpEndpoint) {
      opts.endpoint = bucketPrimarySpEndpoint;
      opts.spEndpoint = bucketPrimarySpEndpoint;
    }
    const uploadRes = await client.object.delegateUploadObject(opts, authType);

    if (uploadRes.code === 0 || uploadRes.statusCode === 200) {
      out({ id, ok: true, hash: objectName, size: data.length });
    } else {
      out({ id, ok: false,
            error: `Upload failed: code=${uploadRes.code}, msg=${uploadRes.message}, sp=${bucketPrimarySpAddr}` });
    }
  } catch (e) {
    out({ id, ok: false,
          error: `put failed: ${e.message} (sp=${bucketPrimarySpAddr})` });
  }
}

async function doGet(id, objectName) {
  try {
    const res = await client.object.getObject(
      { bucketName: BUCKET, objectName },
      authType,
    );

    if (res.code !== 0) {
      const msg = res.message || "unknown error";
      if (msg.includes("not found") || msg.includes("No such object")) {
        out({ id, ok: false, error: "not_found" });
      } else {
        out({ id, ok: false, error: `get failed: ${msg}` });
      }
      return;
    }

    if (res.body) {
      let data;
      if (typeof res.body.arrayBuffer === "function") {
        data = Buffer.from(await res.body.arrayBuffer());
      } else if (Buffer.isBuffer(res.body)) {
        data = res.body;
      } else if (res.body instanceof Uint8Array) {
        data = Buffer.from(res.body);
      } else {
        data = Buffer.from(String(res.body));
      }
      out({ id, ok: true, data_hex: data.toString("hex"), size: data.length });
    } else {
      out({ id, ok: false, error: "not_found" });
    }
  } catch (e) {
    if (e.message && (e.message.includes("not found") || e.message.includes("No such object"))) {
      out({ id, ok: false, error: "not_found" });
    } else {
      out({ id, ok: false, error: `get failed: ${e.message}` });
    }
  }
}

async function doHead(id, objectName) {
  try {
    const res = await client.object.headObject(BUCKET, objectName);
    if (res && res.objectInfo) {
      out({ id, ok: true, exists: true, size: res.objectInfo.payloadSize, status: res.objectInfo.objectStatus });
    } else {
      out({ id, ok: false, exists: false });
    }
  } catch (e) {
    out({ id, ok: false, exists: false, error: e.message });
  }
}

async function doList(id, prefix) {
  try {
    const res = await client.object.listObjects({ bucketName: BUCKET, prefix: prefix || "" });
    const objects = [];
    const items = res.body?.GfSpListObjectsByBucketNameResponse?.Objects
      || res.body?.objects
      || res.GfSpListObjectsByBucketNameResponse?.Objects
      || res.objects
      || res.body
      || [];

    if (Array.isArray(items)) {
      for (const item of items) {
        const info = item.object_info || item.ObjectInfo || item;
        if (info && info.object_name) {
          objects.push({ key: info.object_name, size: parseInt(info.payload_size || "0", 10) });
        }
      }
    }
    out({ id, ok: true, objects });
  } catch (e) {
    out({ id, ok: false, error: `list failed: ${e.message}` });
  }
}

// ── Command loop ──

async function handleCommand(line) {
  let cmd;
  try {
    cmd = JSON.parse(line);
  } catch (e) {
    out({ id: -1, ok: false, error: "Invalid JSON" });
    return;
  }

  const { id, op } = cmd;
  if (!ready && op !== "exit") {
    out({ id, ok: false, error: "Not initialized yet" });
    return;
  }

  try {
    switch (op) {
      case "put":   await doPut(id, cmd.object, cmd.hex); break;
      case "get":   await doGet(id, cmd.object); break;
      case "head":  await doHead(id, cmd.object); break;
      case "list":  await doList(id, cmd.prefix || cmd.object); break;
      case "ping":  out({ id, ok: true, status: "alive" }); break;
      case "exit":  out({ id, ok: true, status: "exiting" }); process.exit(0);
      default:      out({ id, ok: false, error: `Unknown op: ${op}` });
    }
  } catch (e) {
    out({ id, ok: false, error: e.message });
  }
}

// ── Main ──
init().then(() => {
  const rl = createInterface({ input: process.stdin });
  rl.on("line", line => handleCommand(line.trim()));
  rl.on("close", () => process.exit(0));
}).catch(e => {
  out({ id: 0, ok: false, error: `Init failed: ${e.message}` });
  process.exit(1);
});
