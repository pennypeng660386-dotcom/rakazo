import fs from "node:fs/promises";
import path from "node:path";

export class VaultAccessError extends Error {
  constructor(message) {
    super(message);
    this.name = "VaultAccessError";
  }
}

const DEFAULT_ROOT = "/penny-vault";
const DEFAULT_READ_CHARS = 12_000;
const HARD_READ_CAP = 50_000;
const DEFAULT_SEARCH_LIMIT = 10;
const MAX_SEARCH_LIMIT = 10;
const SNIPPET_RADIUS = 120;

const ALLOWED_DIR_PREFIXES = [
  "00 Control Center",
  "02 Projects",
  "04 AI & Skills",
  "05 Knowledge",
];

const ALLOWED_ROOT_FILES = new Set(["Penny AI Vault - README.md"]);

const DENIED_PREFIXES = [
  ".obsidian",
  "00 Inbox",
  "01 Business",
  "03 Customers",
  "06 Output",
  "90 Templates",
  "99 Archive",
];

export function vaultRoot() {
  const root = process.env.PENNY_VAULT_ROOT?.trim() || DEFAULT_ROOT;
  return path.resolve(root);
}

function normalizeRelative(input) {
  if (typeof input !== "string" || !input.trim()) {
    throw new VaultAccessError("relative_path is required");
  }
  let rel = input.trim().replace(/\\/g, "/");
  if (path.isAbsolute(input) || /^[A-Za-z]:[\\/]/.test(input)) {
    throw new VaultAccessError("absolute paths are denied");
  }
  if (rel.includes("\0")) throw new VaultAccessError("invalid path");
  if (rel.startsWith("/")) rel = rel.slice(1);
  const parts = rel.split("/").filter((p) => p && p !== ".");
  if (parts.some((p) => p === ".." || p.startsWith("."))) {
    throw new VaultAccessError("path traversal or hidden segments are denied");
  }
  return parts.join("/");
}

function isDeniedRelative(rel) {
  const top = rel.split("/")[0] ?? "";
  if (DENIED_PREFIXES.some((d) => top === d || rel.startsWith(`${d}/`))) return true;
  if (rel.split("/").some((segment) => segment.startsWith("."))) return true;
  return false;
}

function isAllowedRelative(rel) {
  if (!rel.toLowerCase().endsWith(".md")) return false;
  if (ALLOWED_ROOT_FILES.has(rel)) return true;
  return ALLOWED_DIR_PREFIXES.some((prefix) => rel === prefix || rel.startsWith(`${prefix}/`));
}

export async function resolveAllowedPath(relativePath) {
  const rel = normalizeRelative(relativePath);
  if (isDeniedRelative(rel)) throw new VaultAccessError(`denied path: ${rel}`);
  if (!isAllowedRelative(rel)) throw new VaultAccessError(`path outside allowed roots: ${rel}`);

  const root = vaultRoot();
  const absolute = path.resolve(root, rel);
  const rootReal = await fs.realpath(root).catch(() => root);
  let targetReal;
  try {
    targetReal = await fs.realpath(absolute);
  } catch {
    throw new VaultAccessError(`file not found: ${rel}`);
  }
  const rootPrefix = rootReal.endsWith(path.sep) ? rootReal : rootReal + path.sep;
  if (targetReal !== rootReal && !targetReal.startsWith(rootPrefix)) {
    throw new VaultAccessError("path escapes vault root");
  }
  const st = await fs.stat(targetReal);
  if (!st.isFile()) throw new VaultAccessError("not a file");
  if (!targetReal.toLowerCase().endsWith(".md")) {
    throw new VaultAccessError("non-markdown files are denied");
  }
  return { absolute: targetReal, relative: rel };
}

async function* walkMarkdown(dirAbs, relBase) {
  let entries;
  try {
    entries = await fs.readdir(dirAbs, { withFileTypes: true });
  } catch {
    return;
  }
  for (const entry of entries) {
    if (entry.name.startsWith(".")) continue;
    const childRel = relBase ? `${relBase}/${entry.name}` : entry.name;
    if (isDeniedRelative(childRel)) continue;
    const childAbs = path.join(dirAbs, entry.name);
    if (entry.isDirectory()) {
      if (!ALLOWED_DIR_PREFIXES.some((p) => childRel === p || childRel.startsWith(`${p}/`) || p.startsWith(`${childRel}/`))) {
        // only descend into allowed trees
        if (!ALLOWED_DIR_PREFIXES.some((p) => p.startsWith(childRel + "/") || p === childRel)) {
          continue;
        }
      }
      yield* walkMarkdown(childAbs, childRel);
    } else if (entry.isFile() && entry.name.toLowerCase().endsWith(".md") && isAllowedRelative(childRel)) {
      yield { absolute: childAbs, relative: childRel };
    }
  }
}

function titleFrom(relative, content) {
  const heading = content.match(/^#\s+(.+)$/m);
  if (heading) return heading[1].trim();
  return path.basename(relative, ".md");
}

function snippetAround(content, query) {
  const lower = content.toLowerCase();
  const q = query.toLowerCase();
  const idx = lower.indexOf(q);
  if (idx < 0) {
    return content.slice(0, SNIPPET_RADIUS * 2).replace(/\s+/g, " ").trim();
  }
  const start = Math.max(0, idx - SNIPPET_RADIUS);
  const end = Math.min(content.length, idx + q.length + SNIPPET_RADIUS);
  return content.slice(start, end).replace(/\s+/g, " ").trim();
}

function areaAllowed(rel, allowedArea) {
  if (!allowedArea) return true;
  const area = allowedArea.trim().replace(/\\/g, "/");
  return rel === area || rel.startsWith(`${area}/`);
}

export async function searchVault({ query, allowedArea, limit } = {}) {
  const q = String(query ?? "").trim();
  if (!q) throw new VaultAccessError("query is required");
  const max = Math.min(Math.max(1, Number(limit) || DEFAULT_SEARCH_LIMIT), MAX_SEARCH_LIMIT);
  const root = vaultRoot();
  const results = [];
  const qLower = q.toLowerCase();

  for await (const file of walkMarkdown(root, "")) {
    if (!areaAllowed(file.relative, allowedArea)) continue;
    let content;
    try {
      content = await fs.readFile(file.absolute, "utf8");
    } catch {
      continue;
    }
    const hay = `${file.relative}\n${content}`.toLowerCase();
    if (!hay.includes(qLower)) continue;
    results.push({
      path: file.relative,
      title: titleFrom(file.relative, content),
      snippet: snippetAround(content, q),
    });
    if (results.length >= max) break;
  }
  return results;
}

export async function readVaultFile({ relativePath, maxChars } = {}) {
  const { absolute, relative } = await resolveAllowedPath(relativePath);
  const raw = await fs.readFile(absolute, "utf8");
  const cap = Math.min(Math.max(1, Number(maxChars) || DEFAULT_READ_CHARS), HARD_READ_CAP);
  const truncated = raw.length > cap;
  return {
    path: relative,
    truncated,
    max_chars: cap,
    content: truncated ? raw.slice(0, cap) : raw,
  };
}

function projectCandidates(project) {
  const name = String(project ?? "").trim();
  if (!name) throw new VaultAccessError("project is required");
  const aliases = new Set([name]);
  if (/knowledge\s*os/i.test(name) || /penny\s*knowledge/i.test(name)) {
    aliases.add("Penny Knowledge OS");
  }
  return [...aliases];
}

export async function readCurrentHandoff({ project } = {}) {
  const candidates = projectCandidates(project);
  for (const name of candidates) {
    const currentRel = `02 Projects/${name}-HANDOFF-CURRENT.md`;
    try {
      const file = await readVaultFile({ relativePath: currentRel, maxChars: HARD_READ_CAP });
      return { kind: "current", project: name, ...file };
    } catch {
      // try primary note next
    }
  }
  for (const name of candidates) {
    const primaryRel = `02 Projects/${name}.md`;
    try {
      const file = await readVaultFile({ relativePath: primaryRel, maxChars: HARD_READ_CAP });
      return { kind: "primary", project: name, note: "CURRENT handoff not found; returned primary project note.", ...file };
    } catch {
      // continue
    }
  }
  throw new VaultAccessError(`no CURRENT or primary project note found for: ${candidates.join(", ")}`);
}
