#!/usr/bin/env bash
set -euo pipefail

max_attempts=4
initial_delay_seconds=300
max_delay_seconds=600
sleep_command=sleep

usage() {
  echo "usage: retry-command.sh [options] -- command [args...]" >&2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --max-attempts)
      max_attempts=$2
      shift 2
      ;;
    --initial-delay-seconds)
      initial_delay_seconds=$2
      shift 2
      ;;
    --max-delay-seconds)
      max_delay_seconds=$2
      shift 2
      ;;
    --sleep-command)
      sleep_command=$2
      shift 2
      ;;
    --)
      shift
      break
      ;;
    *)
      usage
      exit 2
      ;;
  esac
done

if [[ $# -eq 0 ]]; then
  usage
  exit 2
fi
if [[ ! $max_attempts =~ ^[1-9][0-9]*$ ]]; then
  echo "--max-attempts must be a positive integer" >&2
  exit 2
fi
for value in "$initial_delay_seconds" "$max_delay_seconds"; do
  if [[ ! $value =~ ^[0-9]+$ ]]; then
    echo "retry delays must be non-negative integers" >&2
    exit 2
  fi
done
if (( initial_delay_seconds > max_delay_seconds )); then
  echo "--initial-delay-seconds must not exceed --max-delay-seconds" >&2
  exit 2
fi

attempt=1
delay_seconds=$initial_delay_seconds
final_status=1

while (( attempt <= max_attempts )); do
  echo "Attempt $attempt/$max_attempts"
  # A command in an `if` condition is exempt from bash `set -e`, so its
  # non-zero status can be captured and retried under GitHub Actions defaults.
  if "$@"; then
    echo "Command succeeded on attempt $attempt."
    exit 0
  else
    final_status=$?
  fi

  if (( attempt == max_attempts )); then
    break
  fi

  echo "Attempt $attempt failed with exit code $final_status; retrying in ${delay_seconds}s."
  "$sleep_command" "$delay_seconds"
  if (( delay_seconds < max_delay_seconds )); then
    delay_seconds=$((delay_seconds * 2))
    if (( delay_seconds > max_delay_seconds )); then
      delay_seconds=$max_delay_seconds
    fi
  fi
  attempt=$((attempt + 1))
done

echo "Command failed after $max_attempts attempts with exit code $final_status." >&2
exit "$final_status"
