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

  // Get primary SP (cached for lifetime)
  const spListRes = await client.sp.getStorageProviders();
  const spArray = Array.isArray(spListRes) ? spListRes : (spListRes.sps || []);
  let primarySP = spArray.find(sp => sp.status === 0 || sp.status === "STATUS_IN_SERVICE") || spArray[0];
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

async function doPut(id, objectName, hexData) {
  const data = Buffer.from(hexData, "hex");

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

    const uploadRes = await client.object.delegateUploadObject(
      {
        bucketName: BUCKET,
        objectName: objectName,
        body: body,
        delegatedOpts: { visibility: VISIBILITY_PRIVATE },
        timeout: 60000,
      },
      authType,
    );

    if (uploadRes.code === 0 || uploadRes.statusCode === 200) {
      out({ id, ok: true, hash: objectName, size: data.length });
    } else {
      out({ id, ok: false, error: `Upload failed: code=${uploadRes.code}, msg=${uploadRes.message}` });
    }
  } catch (e) {
    out({ id, ok: false, error: `put failed: ${e.message}` });
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
