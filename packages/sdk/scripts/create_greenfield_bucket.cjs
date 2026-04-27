#!/usr/bin/env node
/**
 * Create a bucket on BNB Greenfield.
 *
 * Usage:
 *   node scripts/create_greenfield_bucket.cjs [bucket_name]
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
  const candidates = [
    resolve(process.cwd(), ".env"),
    resolve(__dirname, "..", ".env"),
  ];
  for (const envPath of candidates) {
    try {
      const content = readFileSync(envPath, "utf-8");
      for (const line of content.split("\n")) {
        const trimmed = line.trim();
        if (!trimmed || trimmed.startsWith("#")) continue;
        const eqIdx = trimmed.indexOf("=");
        if (eqIdx === -1) continue;
        const key = trimmed.slice(0, eqIdx).trim();
        const val = trimmed.slice(eqIdx + 1).trim();
        if (!process.env[key]) process.env[key] = val;
      }
      return;
    } catch (e) { /* try next */ }
  }
}
loadEnv();

const PRIVATE_KEY = process.env.RUNE_PRIVATE_KEY;
if (!PRIVATE_KEY) {
  console.error("❌ RUNE_PRIVATE_KEY not set");
  process.exit(1);
}

const BUCKET_NAME = process.argv[2] || process.env.RUNE_GREENFIELD_BUCKET || "rune-agent-state";
const NETWORK = process.env.RUNE_GREENFIELD_NETWORK || "testnet";

const CONFIGS = {
  testnet: {
    chainId: "5600",
    chainRpc: "https://gnfd-testnet-fullnode-tendermint-us.bnbchain.org",
  },
  mainnet: {
    chainId: "1017",
    chainRpc: "https://greenfield-chain.bnbchain.org",
  },
};

const config = CONFIGS[NETWORK] || CONFIGS.testnet;

async function main() {
  // Dynamic import for CJS/ESM interop
  const sdk = require("@bnb-chain/greenfield-js-sdk");
  const { ethers } = require("ethers");
  const Long = sdk.Long || require("long");

  const Client = sdk.Client;

  console.log(`\n🪣 Creating Greenfield Bucket`);
  console.log(`   Network: ${NETWORK}`);
  console.log(`   Bucket:  ${BUCKET_NAME}`);
  console.log(`   Chain:   ${config.chainRpc}`);

  // Create wallet from private key
  const wallet = new ethers.Wallet(PRIVATE_KEY);
  const address = wallet.address;
  console.log(`   Account: ${address}\n`);

  // Create Greenfield client
  const client = Client.create(config.chainRpc, String(config.chainId));

  // Get storage providers
  console.log("── Step 1: Discovering storage providers...");
  let spListRes;
  try {
    spListRes = await client.sp.getStorageProviders();
  } catch (e) {
    console.error(`   ❌ Failed to query SPs: ${e.message}`);
    process.exit(1);
  }

  // Debug: show raw response structure
  console.log(`   Raw SP response keys: ${Object.keys(spListRes || {})}`);
  const sps = spListRes.sps || spListRes.storageProviders || spListRes || [];
  const spArray = Array.isArray(sps) ? sps : [];
  console.log(`   SP count: ${spArray.length}`);
  if (spArray.length > 0) {
    console.log(`   First SP keys: ${Object.keys(spArray[0])}`);
    console.log(`   First SP: ${JSON.stringify(spArray[0]).slice(0, 300)}`);
  }

  let primarySP = null;
  for (const sp of spArray) {
    // Handle different field naming conventions
    const status = sp.status ?? sp.Status;
    if (status === 0 || status === "STATUS_IN_SERVICE" || status === "0") {
      primarySP = sp;
      break;
    }
  }
  if (!primarySP && spArray.length > 0) primarySP = spArray[0];

  if (!primarySP) {
    console.error("❌ No storage providers found");
    console.error("   Full response:", JSON.stringify(spListRes).slice(0, 500));
    process.exit(1);
  }

  // Handle different field naming (camelCase vs snake_case)
  const spAddr = primarySP.operatorAddress || primarySP.operator_address;
  const spEndpoint = primarySP.endpoint || primarySP.Endpoint;
  console.log(`   Primary SP: ${spAddr}`);
  console.log(`   SP endpoint: ${spEndpoint}`);

  // Check if bucket already exists
  console.log("\n── Step 2: Checking if bucket exists...");
  try {
    const headRes = await client.bucket.headBucket(BUCKET_NAME);
    if (headRes && headRes.bucketInfo) {
      console.log(`   ✅ Bucket '${BUCKET_NAME}' already exists!`);
      console.log(`   Owner: ${headRes.bucketInfo.owner}`);
      console.log(`   ID: ${headRes.bucketInfo.id}`);
      return;
    }
  } catch (e) {
    console.log(`   Bucket '${BUCKET_NAME}' not found — creating...`);
  }

  // Create bucket
  console.log("\n── Step 3: Creating bucket...");
  try {
    // Visibility enum: 0=UNSPECIFIED, 1=PUBLIC_READ, 2=PRIVATE, 3=INHERIT
    const VISIBILITY_PRIVATE = sdk.VisibilityType
      ? sdk.VisibilityType.VISIBILITY_TYPE_PRIVATE
      : 2;

    const createBucketTx = await client.bucket.createBucket({
      bucketName: BUCKET_NAME,
      creator: address,
      primarySpAddress: spAddr,
      visibility: VISIBILITY_PRIVATE,
      chargedReadQuota: Long.fromInt(0),
      paymentAddress: address,
    });

    // Simulate
    console.log("   Simulating transaction...");
    const simInfo = await createBucketTx.simulate({ denom: "BNB" });
    console.log(`   Gas limit: ${simInfo.gasLimit}`);
    console.log(`   Gas price: ${simInfo.gasPrice}`);
    console.log(`   Gas fee: ${simInfo.gasFee} BNB`);

    // Broadcast
    console.log("   Broadcasting transaction...");
    const broadcastRes = await createBucketTx.broadcast({
      denom: "BNB",
      gasLimit: Number(simInfo.gasLimit),
      gasPrice: simInfo.gasPrice,
      payer: address,
      granter: "",
      privateKey: PRIVATE_KEY.startsWith("0x") ? PRIVATE_KEY : `0x${PRIVATE_KEY}`,
    });

    if (broadcastRes.code === 0) {
      console.log(`\n   ✅ Bucket '${BUCKET_NAME}' created successfully!`);
      console.log(`   Tx hash: ${broadcastRes.transactionHash}`);
    } else {
      console.error(`\n   ❌ Transaction failed: code=${broadcastRes.code}`);
      console.error(`   Raw log: ${broadcastRes.rawLog}`);
      process.exit(1);
    }
  } catch (e) {
    console.error(`\n   ❌ Bucket creation failed: ${e.message}`);
    if (e.message && e.message.includes("already exists")) {
      console.log("   The bucket already exists — this is OK!");
    } else {
      console.error(e);
      process.exit(1);
    }
  }

  // Verify
  console.log("\n── Step 4: Verifying...");
  try {
    const headRes = await client.bucket.headBucket(BUCKET_NAME);
    if (headRes && headRes.bucketInfo) {
      console.log(`   ✅ Bucket '${BUCKET_NAME}' verified on chain`);
      console.log(`   Owner: ${headRes.bucketInfo.owner}`);
      console.log(`   ID: ${headRes.bucketInfo.id}`);
    }
  } catch (e) {
    console.log("   ⏳ Bucket may take a few seconds to finalize on chain");
  }

  console.log("\n✅ Done! You can now run:");
  console.log(`   python demo/test_greenfield.py --mode greenfield\n`);
}

main().catch((e) => {
  console.error("Fatal:", e.message || e);
  process.exit(1);
});
