#!/usr/bin/env bash
# One installer for the whole tool. Run it once with sudo:
#
#   sudo ./install.sh              install everything
#   sudo ./install.sh --uninstall  remove everything installed here (your ~/.sandbox state is kept)
#
# Root is needed for the pf helper and its sudo rule. The command link and the PATH line
# go into your own home directory, and are owned by you.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELPER=/usr/local/libexec/sandbox-pf
SUDOERS=/etc/sudoers.d/sandbox

die() { echo "install.sh: $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run it with sudo: sudo ./install.sh (the tool needs root for pf rules)"
USER_NAME="${SUDO_USER:-}"
[[ -n "$USER_NAME" && "$USER_NAME" != "root" ]] || die "run it through sudo from your own account, not as root"
USER_HOME="$(dscl . -read "/Users/$USER_NAME" NFSHomeDirectory 2>/dev/null | awk '{print $2}')"
[[ -n "$USER_HOME" && -d "$USER_HOME" ]] || die "could not find the home directory for $USER_NAME"
BIN_DIR="$USER_HOME/.local/bin"
LINK="$BIN_DIR/sandbox"

if [[ "${1:-}" == "--uninstall" ]]; then
  rm -f "$LINK" "$SUDOERS" "$HELPER"
  echo "removed the command link, the sudo rule and the helper. Your ~/.sandbox state is untouched."
  exit 0
fi

# Privileged part
install -d -o root -g wheel -m 0755 /usr/local/libexec
install -o root -g wheel -m 0755 "$REPO_DIR/privileged/sandbox-pf" "$HELPER"

tmp="$(mktemp)"
cat > "$tmp" <<EOF
# Installed by sandbox. Lets $USER_NAME run load, show and the network log without a password.
# flush is deliberately not listed, so taking down a sandbox's containment asks for a password.
$USER_NAME ALL=(root) NOPASSWD: $HELPER load *, $HELPER show *, $HELPER capture-start *, $HELPER capture-stop *
EOF
visudo -cf "$tmp" >/dev/null || { rm -f "$tmp"; die "generated sudo rule is invalid; nothing was changed in /etc/sudoers.d"; }
install -o root -g wheel -m 0440 "$tmp" "$SUDOERS"
rm -f "$tmp"

# Per-user part, owned by $USER_NAME
install -d -o "$USER_NAME" -g staff -m 0755 "$BIN_DIR"
ln -sfn "$REPO_DIR/sandbox" "$LINK"
chown -h "$USER_NAME" "$LINK"

# Which shell does this account use? Read it from the account record, not from $SHELL,
# because sudo runs this script with root's environment.
login_shell="$(dscl . -read "/Users/$USER_NAME" UserShell 2>/dev/null | awk '{print $2}')"
shell_name="$(basename "${login_shell:-/bin/zsh}")"
case "$shell_name" in
  zsh)  profile="$USER_HOME/.zshrc";                 path_line="export PATH=\"$BIN_DIR:\$PATH\""; reload="source $USER_HOME/.zshrc" ;;
  bash) profile="$USER_HOME/.bash_profile";          path_line="export PATH=\"$BIN_DIR:\$PATH\""; reload="source $USER_HOME/.bash_profile" ;;
  fish) profile="$USER_HOME/.config/fish/config.fish"; path_line="fish_add_path -g \"$BIN_DIR\""; reload="source $USER_HOME/.config/fish/config.fish" ;;
  *)    profile="$USER_HOME/.profile";               path_line="export PATH=\"$BIN_DIR:\$PATH\""; reload=". $USER_HOME/.profile" ;;
esac

if ! grep -qxF "$path_line" "$profile" 2>/dev/null; then
  echo "$BIN_DIR is not on your PATH yet ($shell_name)."
  read -r -p "Add it to $profile now? [y/N] " answer </dev/tty || answer="n"
  if [[ "$answer" =~ ^[Yy]$ ]]; then
    sudo -u "$USER_NAME" mkdir -p "$(dirname "$profile")"
    echo "$path_line" >> "$profile"
    chown "$USER_NAME" "$profile"
    PATH_ADDED=1
  else
    echo "add this line to $profile yourself: $path_line"
  fi
fi

echo
echo "installed:"
echo "  $HELPER (root-owned, validates every argument)"
echo "  $SUDOERS (load, show and capture for $USER_NAME, no password)"
echo "  $LINK -> $REPO_DIR/sandbox"
echo
echo "next, in this terminal:"
if [[ "${PATH_ADDED:-0}" == "1" ]]; then
  echo "  $reload              (loads the PATH change into this terminal; other terminals pick it up when opened)"
fi
echo "  sandbox init         (once on this Mac)"
