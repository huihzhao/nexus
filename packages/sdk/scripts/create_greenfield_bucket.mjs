#!/usr/bin/env node
/**
 * Create a bucket on BNB Greenfield.
 *
 * Usage:
 *   node scripts/create_greenfield_bucket.mjs [bucket_name]
 *
 * Environment:
 *   RUNE_PRIVATE_KEY           — wallet private key (0x...)
 *   RUNE_GREENFIELD_BUCKET     — bucket name (default: rune-agent-state)
 *   RUNE_GREENFIELD_NETWORK    — testnet (default) or mainnet
 */

import { Client } from "@bnb-chain/greenfield-js-sdk";
import { ethers } from "ethers";
import { readFileSync } from "fs";
import { resolve } from "path";

// Load .env — search upward from cwd
function loadEnv() {
  const candidates = [
    resolve(process.cwd(), ".env"),
    resolve(process.cwd(), "..", ".env"),
  ];
  for (const envPath of candidates) {
    try {
      const envContent = readFileSync(envPath, "utf-8");
      for (const line of envContent.split("\n")) {
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
    spEndpoint: "https://gnfd-testnet-sp1.bnbchain.org",
  },
  mainnet: {
    chainId: "1017",
    chainRpc: "https://greenfield-chain.bnbchain.org",
    spEndpoint: "https://gnfd-sp1.bnbchain.org",
  },
};

const config = CONFIGS[NETWORK] || CONFIGS.testnet;

async function main() {
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
  const spListRes = await client.sp.getStorageProviders();
  const sps = spListRes.sps || [];

  // Find an active SP
  let primarySP = null;
  for (const sp of sps) {
    if (sp.status === 0 || sp.status === "STATUS_IN_SERVICE") {
      primarySP = sp;
      break;
    }
  }

  if (!primarySP) {
    // Use first SP as fallback
    primarySP = sps[0];
  }

  if (!primarySP) {
    console.error("❌ No storage providers found");
    process.exit(1);
  }
  console.log(`   Primary SP: ${primarySP.operatorAddress}`);
  console.log(`   SP endpoint: ${primarySP.endpoint}`);

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
    // Bucket doesn't exist — proceed to create
    console.log(`   Bucket '${BUCKET_NAME}' not found — creating...`);
  }

  // Create bucket
  console.log("\n── Step 3: Creating bucket...");
  try {
    const createBucketTx = await client.bucket.createBucket({
      bucketName: BUCKET_NAME,
      creator: address,
      primarySpAddress: primarySP.operatorAddress,
      visibility: "VISIBILITY_TYPE_PRIVATE",
      chargedReadQuota: "0",
      paymentAddress: address,
    });

    // Simulate to get gas estimate
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
    if (e.message.includes("already exists")) {
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
  console.error("Fatal:", e);
  process.exit(1);
});
