const hre = require("hardhat");

async function main() {
  const [deployer] = await hre.ethers.getSigners();
  console.log("Deploying with account:", deployer.address);
  console.log("Balance:", hre.ethers.formatEther(await hre.ethers.provider.getBalance(deployer.address)), "BNB");

  // ── ERC-8004 Identity Registry ─────────────────────────────────
  // For testnet demos: deploy MockIdentityRegistry (has public mint)
  // For mainnet: use the real BNBChain-deployed registry
  //   BSC Testnet: 0x8004A818BFB912233c491871b3d84c89A494BD9e
  //   BSC Mainnet: 0xfA09B3397fAC75424422C4D28b1729E3D4f659D7
  let IDENTITY_REGISTRY = process.env.IDENTITY_REGISTRY_ADDRESS;

  if (!IDENTITY_REGISTRY || IDENTITY_REGISTRY === "mock") {
    // Deploy MockIdentityRegistry for testnet demos
    console.log("\nDeploying MockIdentityRegistry (for demo/testing)...");
    const MockIdentityRegistry = await hre.ethers.getContractFactory("MockIdentityRegistry");
    const mockRegistry = await MockIdentityRegistry.deploy();
    await mockRegistry.waitForDeployment();
    IDENTITY_REGISTRY = await mockRegistry.getAddress();
    console.log("MockIdentityRegistry deployed at:", IDENTITY_REGISTRY);
    console.log("  (SDK will auto-mint agent identities via mint() function)");
  } else {
    console.log("\nUsing ERC-8004 IdentityRegistry at:", IDENTITY_REGISTRY);
    console.log("  (Agents must be registered externally before using SDK)");
  }

  // ── Deploy AgentStateExtension ─────────────────────────────────
  console.log("\nDeploying AgentStateExtension...");
  const AgentStateExtension = await hre.ethers.getContractFactory("AgentStateExtension");
  const agentState = await AgentStateExtension.deploy(IDENTITY_REGISTRY);
  await agentState.waitForDeployment();
  const agentStateAddr = await agentState.getAddress();
  console.log("AgentStateExtension deployed at:", agentStateAddr);

  // ── Deploy TaskStateManager ────────────────────────────────────
  console.log("\nDeploying TaskStateManager...");
  const TaskStateManager = await hre.ethers.getContractFactory("TaskStateManager");
  const taskManager = await TaskStateManager.deploy(IDENTITY_REGISTRY);
  await taskManager.waitForDeployment();
  const taskManagerAddr = await taskManager.getAddress();
  console.log("TaskStateManager deployed at:", taskManagerAddr);

  // ── Summary ────────────────────────────────────────────────────
  console.log("\n" + "=".repeat(60));
  console.log("DEPLOYMENT COMPLETE");
  console.log("=".repeat(60));
  console.log("Network:               ", hre.network.name);
  console.log("ERC-8004 Identity:     ", IDENTITY_REGISTRY, "(BNBChain)");
  console.log("AgentStateExtension:   ", agentStateAddr);
  console.log("TaskStateManager:      ", taskManagerAddr);
  console.log("=".repeat(60));

  // Write addresses to file for Python SDK to read
  const fs = require("fs");
  const addresses = {
    network: hre.network.name,
    identityRegistry: IDENTITY_REGISTRY,
    agentStateExtension: agentStateAddr,
    taskStateManager: taskManagerAddr,
    deployedAt: new Date().toISOString(),
    deployer: deployer.address,
  };
  fs.writeFileSync(
    "deployments.json",
    JSON.stringify(addresses, null, 2)
  );
  console.log("\nAddresses written to deployments.json");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
