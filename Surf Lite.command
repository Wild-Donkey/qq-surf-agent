#!/bin/bash
cd -- "$(dirname -- "$0")" || exit 1
exec "./Surf Console.command" --lite "$@"
