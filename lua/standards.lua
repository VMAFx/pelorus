-- Pelorus governance commands. No repository-local standards LSP is shipped.
vim.api.nvim_create_user_command("StandardsAudit", function()
  vim.cmd("!make audit")
end, { desc = "Audit repository against declared HISS invariants" })

vim.api.nvim_create_user_command("StandardsCompileContext", function()
  vim.cmd("!make compile-context")
end, { desc = "Compile AGENTS.md cross-agent contexts" })

vim.api.nvim_create_user_command("StandardsVerifyAll", function()
  vim.cmd("!make verify-all")
end, { desc = "Run full standards verification pipeline" })
