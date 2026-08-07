#!/usr/bin/env bash
# Symlink the plugin into the SwiftBar plugin folder so `git pull` updates it
# in place, and seed the personal config on first run.
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
plugin="devstacks.5s.py"

# SwiftBar stores its plugin folder in its preferences; fall back to the
# location this repo was developed against.
dir="$(defaults read com.ameba.SwiftBar PluginDirectory 2>/dev/null || true)"
dir="${dir:-$HOME/.swiftbar-plugins}"

if [ ! -d "$dir" ]; then
  echo "SwiftBar plugin folder not found: $dir" >&2
  echo "Install SwiftBar and set its plugin folder, then re-run." >&2
  exit 1
fi

chmod +x "$repo/$plugin"
ln -sfn "$repo/$plugin" "$dir/$plugin"
echo "Linked $dir/$plugin -> $repo/$plugin"

config="$HOME/.config/devstacks/projects.json"
if [ ! -f "$config" ]; then
  mkdir -p "$(dirname "$config")"
  cp "$repo/projects.example.json" "$config"
  echo "Seeded $config — edit it to describe your stacks."
else
  echo "Kept existing $config"
fi

echo "Done. Refresh SwiftBar (or run: open -g 'swiftbar://refreshallplugins')."
