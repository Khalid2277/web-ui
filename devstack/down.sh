#!/bin/sh
docker rm -f farm-console farm-sitl >/dev/null 2>&1 || true
echo "Stopped."
