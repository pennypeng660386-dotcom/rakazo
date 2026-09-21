# Penny Knowledge MCP (Rakazo adapter)

Read-only stdio MCP bridge from Rakazo to the canonical Penny Obsidian Vault.

Runtime tools:
- `penny_vault_search`
- `penny_vault_read`
- `penny_context_current`

Security boundaries:
- read-only Markdown access
- no write/delete/move/rename/shell tools
- allowed Vault areas are enforced in `src/vault.mjs`
- the Vault is mounted read-only at `/penny-vault`
- canonical routing policy remains `04 AI & Skills/Skills/Penny Context Router Skill.md`

Deployment uses `infra/compose/docker-compose.penny-knowledge.yml` and the host-only `.env` value `PENNY_VAULT_HOST_PATH`.

Register the MCP through Rakazo's supported Integrations / `add_mcp_server` path with:
- name: `Penny Knowledge`
- transport: `stdio`
- command: `/usr/local/bin/node`
- args: `/app/tools/penny-knowledge-mcp/src/index.mjs`
- assign to the Penny bot

Do not register by writing directly to PostgreSQL. Do not copy Vault contents into Rakazo Memory.