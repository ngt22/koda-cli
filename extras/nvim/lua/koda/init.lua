local M = {}

local function run_koda_list_cmd()
  local raw = vim.fn.system("NO_COLOR=1 koda l --rows 1 --truncate 0 2>/dev/null")
  if vim.v.shell_error == 0 then
    return raw
  end
  raw = vim.fn.system("NO_COLOR=1 koda ls --rows 1 --truncate 0 2>/dev/null")
  if vim.v.shell_error == 0 then
    return raw
  end
  return nil
end

local function collect_indices(raw)
  local indices = {}
  for line in raw:gmatch("[^\n]+") do
    local idx = line:match("^%s*(%d+)%s+")
    if idx then
      table.insert(indices, idx)
    end
  end
  return indices
end

local function format_picker_line(idx, content)
  local one_line = (content or ""):gsub("\r", ""):gsub("%s*\n%s*", " | ")
  one_line = one_line:gsub("%s+", " "):gsub("^ +", ""):gsub(" +$", "")
  if one_line == "" then
    one_line = "(empty)"
  end
  return idx, (idx .. " " .. one_line)
end

local function get_picker_entries()
  local raw = run_koda_list_cmd()
  if not raw then
    return nil
  end

  local entries = {}
  for _, idx in ipairs(collect_indices(raw)) do
    local content = vim.fn.system({ "koda", "raw", idx })
    if vim.v.shell_error == 0 then
      local parsed_idx, display = format_picker_line(idx, content)
      table.insert(entries, { idx = parsed_idx, display = display })
    end
  end
  return entries
end

local function get_entry_labels(entries)
  local labels = {}
  for _, e in ipairs(entries) do
    table.insert(labels, e.display)
  end
  return labels
end

local function idx_from_selected_label(selected_label)
  local normalized = selected_label:gsub("%s+", " "):gsub("^ +", ""):gsub(" +$", "")
  local idx = normalized:match("^(%d+)%s+")
  return idx
end

local function insert_below(lines)
  vim.api.nvim_put(lines, "l", true, true)
end

local function get_visual_selection()
  local start_mark = vim.api.nvim_buf_get_mark(0, "<")
  local end_mark = vim.api.nvim_buf_get_mark(0, ">")

  local s_line, s_col = start_mark[1], start_mark[2]
  local e_line, e_col = end_mark[1], end_mark[2]

  -- Fallback for cases where visual marks are not yet available.
  if s_line == 0 or e_line == 0 then
    local vpos = vim.fn.getpos("v")
    local cpos = vim.fn.getpos(".")
    if vpos[2] == 0 or cpos[2] == 0 then
      return ""
    end
    s_line, s_col = vpos[2], math.max(vpos[3] - 1, 0)
    e_line, e_col = cpos[2], math.max(cpos[3] - 1, 0)
  end

  if s_line > e_line or (s_line == e_line and s_col > e_col) then
    s_line, e_line = e_line, s_line
    s_col, e_col = e_col, s_col
  end

  local vmode = vim.fn.visualmode()
  if vmode == "V" then
    local buf_lines = vim.api.nvim_buf_get_lines(0, s_line - 1, e_line, false)
    return table.concat(buf_lines, "\n")
  end

  local text_lines = vim.api.nvim_buf_get_text(0, s_line - 1, s_col, e_line - 1, e_col + 1, {})
  return table.concat(text_lines, "\n")
end

function M.pick_and_insert()
  local ok, fzf = pcall(require, "fzf-lua")
  if not ok then
    vim.notify("koda: fzf-lua is required", vim.log.levels.ERROR)
    return
  end

  local entries = get_picker_entries()
  if not entries then
    vim.notify("koda: failed to run `koda l`", vim.log.levels.ERROR)
    return
  end

  if #entries == 0 then
    vim.notify("koda: no entries found", vim.log.levels.WARN)
    return
  end

  fzf.fzf_exec(
    get_entry_labels(entries),
    {
      prompt = "Koda> ",
      actions = {
        ["default"] = function(selected)
          if not selected or not selected[1] then return end
          local idx = idx_from_selected_label(selected[1])
          if not idx then
            return
          end
          local content = vim.fn.system({ "koda", "raw", idx })
          content = content:gsub("\n$", "")
          insert_below(vim.split(content, "\n", { plain = true }))
        end,
      },
    }
  )
end

function M.add_selection()
  local content = get_visual_selection()
  if content == "" then
    vim.notify("koda: no selection", vim.log.levels.WARN)
    return
  end
  if not content:match("%S") then
    vim.notify("koda: selection is whitespace only", vim.log.levels.WARN)
    return
  end

  vim.ui.input({ prompt = "Tags (optional): " }, function(tags)
    if tags == nil then return end
    local cmd = { "koda", "add" }
    if tags ~= "" then
      vim.list_extend(cmd, { "-t", tags })
    end
    local result = vim.fn.system(cmd, content)
    if vim.v.shell_error ~= 0 then
      vim.notify("koda: add failed — " .. result, vim.log.levels.ERROR)
      return
    end
    if result:find("Saved", 1, true) then
      vim.notify("koda: saved", vim.log.levels.INFO)
    elseif result:find("Aborted", 1, true) then
      vim.notify("koda: add aborted (empty content)", vim.log.levels.WARN)
    else
      vim.notify("koda: add result — " .. result, vim.log.levels.WARN)
    end
  end)
end

function M.list()
  local raw = run_koda_list_cmd()
  if not raw then
    vim.notify("koda: failed to run `koda l`", vim.log.levels.ERROR)
    return
  end
  local output = vim.split(raw, "\n", { plain = true, trimempty = true })

  vim.cmd("botright new")
  local buf = vim.api.nvim_get_current_buf()
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, output)
  vim.bo[buf].modifiable = false
  vim.bo[buf].buftype = "nofile"
  vim.bo[buf].bufhidden = "wipe"
  vim.keymap.set("n", "q", "<cmd>close<cr>", { buffer = buf, silent = true })
  vim.cmd("resize " .. math.min(#output + 1, 20))
end

function M.setup()
  local ok, wk = pcall(require, "which-key")
  if ok then
    wk.add({ { "<leader>k", group = "[K]oda" } })
  end

  vim.keymap.set("n", "<leader>ki", M.pick_and_insert, { desc = "[K]oda [I]nsert" })
  vim.keymap.set("x", "<leader>ka", M.add_selection,   { desc = "[K]oda [A]dd selection" })
  vim.keymap.set("n", "<leader>kl", M.list,            { desc = "[K]oda [L]ist" })
end

return M
