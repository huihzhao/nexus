const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("Rune Contracts", function () {
  let identityRegistry;
  let agentStateExt;
  let taskManager;
  let owner, other;
  const AGENT_ID = 1;

  // ── Mock ERC-8004 Identity Registry ────────────────────────────
  // In tests we deploy a minimal mock; on testnet we use the real one.

  before(async function () {
    [owner, other] = await ethers.getSigners();

    // Deploy mock IdentityRegistry
    const MockRegistry = await ethers.getContractFactory("MockIdentityRegistry");
    identityRegistry = await MockRegistry.deploy();
    await identityRegistry.waitForDeployment();

    // Mint agent NFT (tokenId=1) to owner
    await identityRegistry.mint(owner.address, AGENT_ID);

    // Deploy our contracts pointing to mock registry
    const registryAddr = await identityRegistry.getAddress();

    const AgentStateExtension = await ethers.getContractFactory("AgentStateExtension");
    agentStateExt = await AgentStateExtension.deploy(registryAddr);
    await agentStateExt.waitForDeployment();

    const TaskStateManager = await ethers.getContractFactory("TaskStateManager");
    taskManager = await TaskStateManager.deploy(registryAddr);
    await taskManager.waitForDeployment();
  });

  // ── AgentStateExtension ────────────────────────────────────────

  describe("AgentStateExtension", function () {
    it("should update state root", async function () {
      const stateRoot = ethers.keccak256(ethers.toUtf8Bytes("session-snapshot-1"));
      const runtime = ethers.Wallet.createRandom().address;

      const tx = await agentStateExt.updateStateRoot(AGENT_ID, stateRoot, runtime);
      const receipt = await tx.wait();
      const block = await ethers.provider.getBlock(receipt.blockNumber);

      await expect(tx)
        .to.emit(agentStateExt, "StateRootUpdated")
        .withArgs(AGENT_ID, stateRoot, runtime, block.timestamp);

      expect(await agentStateExt.resolveStateRoot(AGENT_ID)).to.equal(stateRoot);
      expect(await agentStateExt.hasState(AGENT_ID)).to.be.true;
    });

    it("should track active runtime", async function () {
      const runtime1 = ethers.Wallet.createRandom().address;
      const runtime2 = ethers.Wallet.createRandom().address;
      const hash = ethers.keccak256(ethers.toUtf8Bytes("state-2"));

      await agentStateExt.updateStateRoot(AGENT_ID, hash, runtime1);
      let state = await agentStateExt.getAgentState(AGENT_ID);
      expect(state.activeRuntime).to.equal(runtime1);

      await expect(agentStateExt.setActiveRuntime(AGENT_ID, runtime2))
        .to.emit(agentStateExt, "RuntimeChanged")
        .withArgs(AGENT_ID, runtime1, runtime2);

      state = await agentStateExt.getAgentState(AGENT_ID);
      expect(state.activeRuntime).to.equal(runtime2);
    });

    it("should reject non-owner", async function () {
      const hash = ethers.keccak256(ethers.toUtf8Bytes("bad"));
      const runtime = ethers.Wallet.createRandom().address;

      await expect(
        agentStateExt.connect(other).updateStateRoot(AGENT_ID, hash, runtime)
      ).to.be.revertedWithCustomError(agentStateExt, "NotAgentOwner");
    });

    it("should reject unregistered agent", async function () {
      const hash = ethers.keccak256(ethers.toUtf8Bytes("bad"));
      const runtime = ethers.Wallet.createRandom().address;

      // ownerOf(999) reverts in the mock registry with "ERC721: invalid token ID"
      // because tokenId 999 was never minted. This correctly prevents access.
      await expect(
        agentStateExt.updateStateRoot(999, hash, runtime)
      ).to.be.reverted;
    });
  });

  // ── TaskStateManager ───────────────────────────────────────────

  describe("TaskStateManager", function () {
    const TASK_ID = ethers.keccak256(ethers.toUtf8Bytes("task-analyst-abc123"));

    it("should create task", async function () {
      const tx = await taskManager.createTask(TASK_ID, AGENT_ID);
      const receipt = await tx.wait();
      const block = await ethers.provider.getBlock(receipt.blockNumber);

      await expect(tx)
        .to.emit(taskManager, "TaskCreated")
        .withArgs(TASK_ID, AGENT_ID, block.timestamp);

      expect(await taskManager.taskExists(TASK_ID)).to.be.true;
      expect(await taskManager.getAgentTaskCount(AGENT_ID)).to.equal(1);
    });

    it("should reject duplicate task", async function () {
      await expect(
        taskManager.createTask(TASK_ID, AGENT_ID)
      ).to.be.revertedWithCustomError(taskManager, "TaskAlreadyExists");
    });

    it("should update task with optimistic concurrency", async function () {
      const hash1 = ethers.keccak256(ethers.toUtf8Bytes("task-payload-v1"));

      // Update from version 0 → 1 (status: Running)
      await expect(taskManager.updateTask(TASK_ID, hash1, 1, 0)) // status 1 = Running
        .to.emit(taskManager, "TaskUpdated");

      const task = await taskManager.getTask(TASK_ID);
      expect(task.version).to.equal(1);
      expect(task.status).to.equal(1); // Running
      expect(task.stateHash).to.equal(hash1);
    });

    it("should reject version conflict", async function () {
      const hash2 = ethers.keccak256(ethers.toUtf8Bytes("task-payload-v2"));

      // Try to update with wrong version (0, but current is 1)
      await expect(
        taskManager.updateTask(TASK_ID, hash2, 2, 0) // expected=0, actual=1
      ).to.be.revertedWithCustomError(taskManager, "VersionConflict");
    });

    it("should complete task lifecycle", async function () {
      const hash2 = ethers.keccak256(ethers.toUtf8Bytes("task-payload-v2"));

      // Update with correct version: 1 → 2 (status: Completed)
      await taskManager.updateTask(TASK_ID, hash2, 2, 1); // status 2 = Completed

      const task = await taskManager.getTask(TASK_ID);
      expect(task.version).to.equal(2);
      expect(task.status).to.equal(2); // Completed
    });

    it("should reject non-owner update", async function () {
      const hash = ethers.keccak256(ethers.toUtf8Bytes("bad"));
      await expect(
        taskManager.connect(other).updateTask(TASK_ID, hash, 1, 2)
      ).to.be.revertedWithCustomError(taskManager, "NotAgentOwner");
    });

    it("should enumerate agent tasks", async function () {
      const taskIds = await taskManager.getAgentTaskIds(AGENT_ID);
      expect(taskIds.length).to.equal(1);
      expect(taskIds[0]).to.equal(TASK_ID);
    });
  });

  // ── Helper ─────────────────────────────────────────────────────

  async function getTimestamp() {
    const block = await ethers.provider.getBlock("latest");
    return block.timestamp;
  }
});
