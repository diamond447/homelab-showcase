#!/usr/bin/env bash
# Claude Code status line: model | effort | dir | branch | ctx% | rate limits
# Reads the status line JSON on stdin; any missing field is simply omitted.

input=$(cat)
j() { printf '%s' "$input" | jq -r "$1 // empty" 2>/dev/null; }

model=$(j '.model.display_name // .model.id')
effort=$(j '.effort.level // .effort // .reasoning_effort')
dir=$(j '.workspace.current_dir // .cwd')
ctx=$(j '.context_window.used_percentage')
five=$(j '.rate_limits.five_hour.used_percentage')
seven=$(j '.rate_limits.seven_day.used_percentage')

branch=""
if [ -n "$dir" ] && [ -d "$dir" ]; then
  branch=$(GIT_OPTIONAL_LOCKS=0 git -C "$dir" symbolic-ref --short -q HEAD 2>/dev/null \
    || GIT_OPTIONAL_LOCKS=0 git -C "$dir" rev-parse --short HEAD 2>/dev/null)
fi
[ -n "$dir" ] && dir="${dir/#$HOME/~}"

pct() { printf '%.0f' "$1" 2>/dev/null || printf '%s' "$1"; }

parts=()
[ -n "$model" ]  && parts+=($'\e[36m'"$model"$'\e[0m')
[ -n "$effort" ] && parts+=($'\e[35m'"effort:$effort"$'\e[0m')
[ -n "$dir" ]    && parts+=($'\e[34m'"$dir"$'\e[0m')
[ -n "$branch" ] && parts+=($'\e[32m'"$branch"$'\e[0m')
[ -n "$ctx" ]    && parts+=($'\e[33m'"ctx:$(pct "$ctx")%"$'\e[0m')
rl=""
[ -n "$five" ]  && rl="5h:$(pct "$five")%"
[ -n "$seven" ] && rl="${rl:+$rl }7d:$(pct "$seven")%"
[ -n "$rl" ]     && parts+=($'\e[31m'"$rl"$'\e[0m')

out=""
for p in "${parts[@]}"; do out="${out:+$out | }$p"; done
printf '%s' "$out"
