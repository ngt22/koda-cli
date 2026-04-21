# koda.lua — Neovim integration for koda

Insert and manage [koda](https://github.com/your-org/koda) snippets from within Neovim.

## Requirements

- [koda](https://github.com/your-org/koda) installed and available in `$PATH`
- [fzf-lua](https://github.com/ibhagwan/fzf-lua) (required for `<leader>ki`)
- Neovim 0.9+

## Installation

Copy `koda.lua` to your Neovim config's Lua path, for example:

```
~/.config/nvim/lua/custom/koda.lua
```

Then call `setup()` somewhere in your config (e.g. `~/.config/nvim/lua/custom/config.lua`):

```lua
require("custom.koda").setup()
```

### With lazy.nvim (local path)

If you have cloned the koda repository locally, you can point lazy.nvim at the
`extras/nvim` directory directly:

```lua
{
  dir = "/path/to/koda/extras/nvim",
  name = "koda-nvim",
  config = function()
    require("koda").setup()
  end,
}
```

> **Note:** when using `dir`, place `koda.lua` at `extras/nvim/lua/koda.lua`
> so that lazy.nvim can find it on the runtime path. Alternatively, just copy
> the file as described above.

## Keymaps

| Key           | Mode   | Action                                          |
|---------------|--------|-------------------------------------------------|
| `<leader>ki`  | normal | Pick a koda entry with fzf-lua and insert below cursor |
| `<leader>ka`  | visual | Add the selected text to koda (prompts for tags) |
| `<leader>kl`  | normal | Show `koda ls` output in a split (press `q` to close) |

All three keys are grouped under `<leader>k` and appear in which-key if it is
installed.

## Usage

### Insert a snippet

In normal mode, press `<leader>ki`. A fzf-lua picker opens showing all koda
entries. Select one and press `<Enter>` — the full content (`koda raw <idx>`)
is inserted as new lines below the cursor.

### Save a selection

Visually select text, then press `<leader>ka`. You will be prompted for
optional comma-separated tags. Press `<Enter>` to save (leave blank to skip
tags). The selection is passed to `koda add` via stdin.

### Browse entries

Press `<leader>kl` to open a read-only split showing `koda ls` output. Press
`q` to close it.
