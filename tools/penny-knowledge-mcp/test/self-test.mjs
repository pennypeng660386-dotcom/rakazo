import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  searchVault,
  readVaultFile,
  readCurrentHandoff,
  VaultAccessError,
  vaultRoot,
} from "../src/vault.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

async function expectDeny(label, fn) {
  try {
    await fn();
    throw new Error(`${label}: expected denial`);
  } catch (error) {
    if (!(error instanceof VaultAccessError)) throw error;
    console.log(`DENY OK: ${label} -> ${error.message}`);
  }
}

function fileMeta(abs) {
  const st = fs.statSync(abs);
  const hash = crypto.createHash("sha256").update(fs.readFileSync(abs)).digest("hex");
  return { mtimeMs: st.mtimeMs, size: st.size, hash };
}

async function main() {
  const root = vaultRoot();
  console.log("VAULT_ROOT", root);
  assert(fs.existsSync(root), "vault root missing");

  const currentRel = "02 Projects/Penny Knowledge OS-HANDOFF-CURRENT.md";
  const routerRel = "04 AI & Skills/Skills/Penny Context Router Skill.md";
  const currentAbs = path.join(root, currentRel);
  const routerAbs = path.join(root, routerRel);
  const before = {
    current: fileMeta(currentAbs),
    router: fileMeta(routerAbs),
  };
  console.log("BEFORE_HASH current", before.current.hash.slice(0, 16));
  console.log("BEFORE_HASH router", before.router.hash.slice(0, 16));

  const searchOs = await searchVault({ query: "Penny Knowledge OS", limit: 10 });
  assert(
    searchOs.some((r) => r.path === currentRel),
    "search must find CURRENT handoff",
  );
  console.log("SEARCH OK Penny Knowledge OS ->", searchOs.slice(0, 3).map((r) => r.path));

  const current = await readVaultFile({ relativePath: currentRel });
  assert(current.content.includes("V05.2"), "CURRENT must include V05.2");
  assert(current.path === currentRel, "path echo mismatch");
  console.log("READ OK CURRENT includes V05.2");

  const searchRouter = await searchVault({ query: "Penny Context Router", limit: 10 });
  assert(
    searchRouter.some((r) => r.path === routerRel),
    "search must find Context Router skill",
  );
  const router = await readVaultFile({ relativePath: routerRel });
  assert(router.content.length > 100, "router skill too short");
  console.log("READ OK Context Router skill");

  const viaCurrent = await readCurrentHandoff({ project: "Penny Knowledge OS" });
  assert(viaCurrent.kind === "current", "penny_context_current should prefer CURRENT");
  assert(viaCurrent.content.includes("V05.2"), "context current missing V05.2");
  console.log("CURRENT TOOL OK");

  await expectDeny("traversal", () => readVaultFile({ relativePath: "../README.md" }));
  await expectDeny("obsidian", () => readVaultFile({ relativePath: ".obsidian/app.json" }));
  await expectDeny("customers", () =>
    readVaultFile({ relativePath: "03 Customers/anything.md" }),
  );
  await expectDeny("archive", () => readVaultFile({ relativePath: "99 Archive/x.md" }));
  await expectDeny("non-md", () =>
    readVaultFile({ relativePath: "02 Projects/Penny Knowledge OS-HANDOFF-CURRENT.txt" }),
  );

  const after = {
    current: fileMeta(currentAbs),
    router: fileMeta(routerAbs),
  };
  assert(before.current.hash === after.current.hash, "CURRENT file mutated");
  assert(before.router.hash === after.router.hash, "Router skill mutated");
  assert(before.current.mtimeMs === after.current.mtimeMs, "CURRENT mtime changed");
  assert(before.router.mtimeMs === after.router.mtimeMs, "Router mtime changed");
  console.log("READ-ONLY PROOF OK (hash+mtime unchanged)");
  console.log("SELF_TEST PASS");
}

main().catch((error) => {
  console.error("SELF_TEST FAIL", error);
  process.exit(1);
});
