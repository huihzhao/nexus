#!/usr/bin/env node
/**
 * Greenfield object operations helper — called by Python greenfield.py.
 *
 * Usage:
 *   node scripts/greenfield_ops.cjs put <object_name> <hex_data>
 *   node scripts/greenfield_ops.cjs get <object_name>
 *   node scripts/greenfield_ops.cjs head <object_name>
 *
 * Output (JSON to stdout):
 *   { "ok": true, "hash": "...", "size": 42 }
 *   { "ok": true, "data_hex": "48656c6c6f..." }
 *   { "ok": false, "error": "..." }
 *
 * Environment:
 *   RUNE_PRIVATE_KEY           — wallet private key (0x...)
 *   RUNE_GREENFIELD_BUCKET     — bucket name (default: rune-agent-state)
 *   RUNE_GREENFIELD_NETWORK    — testnet (default) or mainnet
 */

const { readFileSync } = require("fs");
const { resolve } = require("path");

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

const PRIVATE_KEY = process.env.RUNE_PRIVATE_KEY;
const BUCKET = process.env.RUNE_GREENFIELD_BUCKET || "rune-agent-state";
const NETWORK = process.env.RUNE_GREENFIELD_NETWORK || "testnet";

const CONFIGS = {
  testnet: { chainId: "5600", chainRpc: "https://gnfd-testnet-fullnode-tendermint-us.bnbchain.org" },
  mainnet: { chainId: "1017", chainRpc: "https://greenfield-chain.bnbchain.org" },
};

async function main() {
  if (!PRIVATE_KEY) {
    out({ ok: false, error: "RUNE_PRIVATE_KEY not set" });
    process.exit(1);
  }

  const [, , op, objectName, hexData] = process.argv;
  if (!op || !objectName) {
    out({ ok: false, error: "Usage: greenfield_ops.cjs <put|get|head> <object_name> [hex_data]" });
    process.exit(1);
  }

  const sdk = require("@bnb-chain/greenfield-js-sdk");
  const { ethers } = require("ethers");
  const Long = sdk.Long || require("long");

  const config = CONFIGS[NETWORK] || CONFIGS.testnet;
  const client = sdk.Client.create(config.chainRpc, String(config.chainId));
  const wallet = new ethers.Wallet(PRIVATE_KEY);
  const address = wallet.address;
  const pk = PRIVATE_KEY.startsWith("0x") ? PRIVATE_KEY : `0x${PRIVATE_KEY}`;

  // Get primary SP
  const spListRes = await client.sp.getStorageProviders();
  const spArray = Array.isArray(spListRes) ? spListRes : (spListRes.sps || []);
  let primarySP = spArray.find(sp => sp.status === 0 || sp.status === "STATUS_IN_SERVICE") || spArray[0];
  if (!primarySP) {
    out({ ok: false, error: "No storage providers found" });
    process.exit(1);
  }
  const spAddr = primarySP.operatorAddress || primarySP.operator_address;
  const spEndpoint = primarySP.endpoint;

  // Collect secondary SP addresses (all SPs except primary, for redundancy)
  const secondarySpAddresses = spArray
    .filter(sp => {
      const addr = sp.operatorAddress || sp.operator_address;
      return addr && addr !== spAddr;
    })
    .map(sp => sp.operatorAddress || sp.operator_address);

  if (op === "put") {
    await doPut(client, sdk, Long, address, pk, spAddr, secondarySpAddresses, objectName, hexData);
  } else if (op === "get") {
    await doGet(client, address, pk, spAddr, spEndpoint, objectName);
  } else if (op === "head") {
    await doHead(client, objectName);
  } else if (op === "list") {
    await doList(client, address, pk, objectName);
  } else {
    out({ ok: false, error: `Unknown op: ${op}` });
  }
}

async function doPut(client, sdk, Long, address, pk, spAddr, secondarySpAddresses, objectName, hexData) {
  if (!hexData) {
    out({ ok: false, error: "put requires hex_data argument" });
    process.exit(1);
  }

  const data = Buffer.from(hexData, "hex");

  // Check if object already exists
  try {
    const headRes = await client.object.headObject(BUCKET, objectName);
    if (headRes && headRes.objectInfo) {
      out({ ok: true, hash: objectName, size: data.length, existed: true });
      return;
    }
  } catch (e) {
    // Object doesn't exist, proceed
  }

  const VISIBILITY_PRIVATE = sdk.VisibilityType
    ? sdk.VisibilityType.VISIBILITY_TYPE_PRIVATE
    : 2;

  const authType = { type: "ECDSA", privateKey: pk };

  try {
    // Use delegateUploadObject — SP handles CreateObject tx + upload in one step.
    // This avoids needing to compute expectChecksums (RS erasure coding hashes).
    //
    // SDK internals:
    //   - delegateUploadObject checks body.size to decide resumable vs single upload
    //   - putObject -> getPutObjectMetaInfo uses body.size for payload_size query param
    //   - upload() calls superagent .send(file) — Buffer works in Node.js
    //   - So we need a Buffer with a .size property added
    const body = Buffer.from(data);
    body.size = body.length;  // SDK reads .size (File/Blob API)
    body.type = "application/octet-stream";  // SDK reads .type for content-type

    const uploadRes = await client.object.delegateUploadObject(
      {
        bucketName: BUCKET,
        objectName: objectName,
        body: body,
        delegatedOpts: {
          visibility: VISIBILITY_PRIVATE,
        },
        timeout: 60000,
      },
      authType,
    );

    if (uploadRes.code === 0 || uploadRes.statusCode === 200) {
      out({ ok: true, hash: objectName, size: data.length });
    } else {
      out({ ok: false, error: `Upload failed: code=${uploadRes.code}, msg=${uploadRes.message}` });
    }
  } catch (e) {
    out({ ok: false, error: `put failed: ${e.message}`, stack: e.stack });
  }
}

async function doGet(client, address, pk, spAddr, spEndpoint, objectName) {
  const authType = { type: "ECDSA", privateKey: pk };

  try {
    const res = await client.object.getObject(
      {
        bucketName: BUCKET,
        objectName: objectName,
      },
      authType,
    );

    if (res.code !== 0) {
      const msg = res.message || "unknown error";
      if (msg.includes("not found") || msg.includes("No such object")) {
        out({ ok: false, error: "not_found" });
      } else {
        out({ ok: false, error: `get failed: ${msg}` });
      }
      return;
    }

    if (res.body) {
      // res.body is a Blob (from SDK's getObject)
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
      out({ ok: true, data_hex: data.toString("hex"), size: data.length });
    } else {
      out({ ok: false, error: "Object not found or empty response" });
    }
  } catch (e) {
    if (e.message && (e.message.includes("not found") || e.message.includes("No such object"))) {
      out({ ok: false, error: "not_found" });
    } else {
      out({ ok: false, error: `get failed: ${e.message}`, stack: e.stack });
    }
  }
}

async function doHead(client, objectName) {
  try {
    const res = await client.object.headObject(BUCKET, objectName);
    if (res && res.objectInfo) {
      out({
        ok: true,
        exists: true,
        size: res.objectInfo.payloadSize,
        status: res.objectInfo.objectStatus,
      });
    } else {
      out({ ok: false, exists: false });
    }
  } catch (e) {
    out({ ok: false, exists: false, error: e.message });
  }
}

async function doList(client, address, pk, prefix) {
  const authType = { type: "ECDSA", privateKey: pk };

  try {
    // Use SP listObjects API via the SDK
    const res = await client.object.listObjects({
      bucketName: BUCKET,
      prefix: prefix || "",
    });

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
          objects.push({
            key: info.object_name,
            size: parseInt(info.payload_size || "0", 10),
          });
        }
      }
    }
    out({ ok: true, objects: objects });
  } catch (e) {
    // Fallback: try headObject on known paths
    out({ ok: false, error: `list failed: ${e.message}` });
  }
}

main().catch(e => {
  out({ ok: false, error: e.message || String(e) });
  process.exit(1);
});
