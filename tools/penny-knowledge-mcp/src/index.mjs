/**
 * Penny Knowledge MCP — read-only vault bridge for Rakazo.
 * Resolves @modelcontextprotocol/sdk from the existing Rakazo image (no second framework).
 */
import { createRequire } from "node:module";
import path from "node:path";
import { pathToFileURL } from "node:url";
import {
  searchVault,
  readVaultFile,
  readCurrentHandoff,
  VaultAccessError,
} from "./vault.mjs";

async function loadSdk() {
  const require = createRequire("/app/packages/adapters/package.json");
  const pkgJson = require.resolve("@modelcontextprotocol/sdk/package.json");
  const esmRoot = path.join(path.dirname(pkgJson), "../esm");
  const [{ McpServer }, { StdioServerTransport }] = await Promise.all([
    import(pathToFileURL(path.join(esmRoot, "server/mcp.js")).href),
    import(pathToFileURL(path.join(esmRoot, "server/stdio.js")).href),
  ]);
  let z;
  try {
    z = (await import("zod/v4")).default ?? (await import("zod/v4"));
  } catch {
    const zodPkg = require.resolve("zod/package.json");
    const zodRoot = path.dirname(zodPkg);
    z = (await import(pathToFileURL(path.join(zodRoot, "v4/index.js")).href)).default
      ?? (await import(pathToFileURL(path.join(zodRoot, "index.js")).href));
  }
  return { McpServer, StdioServerTransport, z };
}

function textResult(payload, isError = false) {
  return {
    content: [{ type: "text", text: typeof payload === "string" ? payload : JSON.stringify(payload, null, 2) }],
    isError,
  };
}

function errorResult(error) {
  const message = error instanceof VaultAccessError ? error.message : String(error?.message ?? error);
  return textResult({ error: message }, true);
}

async function main() {
  const { McpServer, StdioServerTransport, z } = await loadSdk();
  const server = new McpServer({
    name: "penny-knowledge",
    version: "0.1.0",
  });

  server.registerTool(
    "penny_vault_search",
    {
      description:
        "Search allowed Penny Knowledge Vault Markdown (paths + content). Returns small snippets only. Does not search denied areas (Customers, Archive, Inbox, .obsidian, etc.).",
      inputSchema: {
        query: z.string().min(1).describe("Case-insensitive search query"),
        allowed_area: z
          .string()
          .optional()
          .describe("Optional area filter, e.g. '02 Projects' or '04 AI & Skills'"),
        limit: z.number().int().positive().max(10).optional().describe("Max results (default 10, max 10)"),
      },
    },
    async ({ query, allowed_area, limit }) => {
      try {
        const results = await searchVault({ query, allowedArea: allowed_area, limit });
        return textResult({ count: results.length, results });
      } catch (error) {
        return errorResult(error);
      }
    },
  );

  server.registerTool(
    "penny_vault_read",
    {
      description:
        "Read one allowed Markdown file from the Penny Knowledge Vault by relative path. Read-only. Output is length-bounded.",
      inputSchema: {
        relative_path: z
          .string()
          .min(1)
          .describe("Relative Markdown path under the vault, e.g. '02 Projects/Foo-HANDOFF-CURRENT.md'"),
        max_chars: z
          .number()
          .int()
          .positive()
          .max(50_000)
          .optional()
          .describe("Max characters to return (default 12000, hard cap 50000)"),
      },
    },
    async ({ relative_path, max_chars }) => {
      try {
        const result = await readVaultFile({ relativePath: relative_path, maxChars: max_chars });
        return textResult(result);
      } catch (error) {
        return errorResult(error);
      }
    },
  );

  server.registerTool(
    "penny_context_current",
    {
      description:
        "Locate a project's CURRENT handoff: prefers '02 Projects/<Project>-HANDOFF-CURRENT.md'. Does not invent missing CURRENT files.",
      inputSchema: {
        project: z
          .string()
          .min(1)
          .describe("Project name or alias, e.g. 'Penny Knowledge OS'"),
      },
    },
    async ({ project }) => {
      try {
        const result = await readCurrentHandoff({ project });
        return textResult(result);
      } catch (error) {
        return errorResult(error);
      }
    },
  );

  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error("penny-knowledge-mcp ready (read-only)");
}

main().catch((error) => {
  console.error("penny-knowledge-mcp failed:", error);
  process.exit(1);
});
