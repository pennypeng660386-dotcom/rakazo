import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import path from "node:path";
import { pathToFileURL } from "node:url";

const require = createRequire("/app/packages/adapters/package.json");
const pkgJson = require.resolve("@modelcontextprotocol/sdk/package.json");
const esmRoot = path.join(path.dirname(pkgJson), "../esm");
const { Client } = await import(pathToFileURL(path.join(esmRoot, "client/index.js")).href);
const { StdioClientTransport } = await import(
  pathToFileURL(path.join(esmRoot, "client/stdio.js")).href
);

const transport = new StdioClientTransport({
  command: "/usr/local/bin/node",
  args: ["/app/tools/penny-knowledge-mcp/src/index.mjs"],
  env: { ...process.env, PENNY_VAULT_ROOT: "/penny-vault" },
  stderr: "pipe",
});

const client = new Client({ name: "penny-bridge-test", version: "0.1.0" });
await client.connect(transport);
const tools = await client.listTools();
console.log(
  "TOOLS",
  tools.tools.map((t) => t.name).join(","),
);

const search = await client.callTool({
  name: "penny_vault_search",
  arguments: { query: "Penny Knowledge OS", limit: 5 },
});
console.log("SEARCH", JSON.stringify(search.content?.[0]?.text ?? search).slice(0, 400));

const read = await client.callTool({
  name: "penny_vault_read",
  arguments: {
    relative_path: "02 Projects/Penny Knowledge OS-HANDOFF-CURRENT.md",
    max_chars: 2000,
  },
});
const readText = read.content?.[0]?.text ?? "";
console.log("READ_HAS_V05_2", readText.includes("V05.2"));

const denied = await client.callTool({
  name: "penny_vault_read",
  arguments: { relative_path: "03 Customers/secret.md" },
});
console.log("DENY_CUSTOMERS", denied.isError === true || String(denied.content?.[0]?.text).includes("denied"));

await client.close();
console.log("MCP_CLIENT_PASS");
