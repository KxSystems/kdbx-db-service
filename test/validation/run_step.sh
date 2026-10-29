#!/bin/sh

set -u

label="$1"
log_file="$2"
shift 2

is_verbose() {
  case "${VERBOSE:-0}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

print_command() {
  printf 'Command:' >&2
  for arg in "$@"; do
    printf ' %s' "$arg" >&2
  done
  printf '\n' >&2
}

mkdir -p "$(dirname "$log_file")"

if is_verbose; then
  printf '%s\n' "-> $label"
  "$@"
  exit $?
fi

if "$@" >>"$log_file" 2>&1; then
  exit 0
else
  rc=$?
fi

printf '\nVALIDATION SETUP FAILED\n' >&2
printf 'Stage: %s\n' "$label" >&2
print_command "$@"
printf 'Full output: %s\n' "$log_file" >&2
printf '%s\n' 'Last output:' >&2
tail -n "${VALIDATION_ERROR_LINES:-30}" "$log_file" >&2 || true
exit "$rc"
