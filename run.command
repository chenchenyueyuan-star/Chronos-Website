#!/bin/zsh
# Always run from this project with the Python containing its dependencies.
cd -- "${0:A:h}" || exit 1
if [[ -x .venv/bin/python ]]; then
  exec .venv/bin/python launch.py "$@"
fi
exec /Users/chenyuan/miniforge3/bin/python launch.py "$@"
