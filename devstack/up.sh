#!/bin/sh
# Developer stack: ArduCopter 4.7.1 SITL (same firmware as the drone) + the farm command center.
#   devstack/up.sh        then open http://localhost:8090   (SIMULATOR banner; data in devstack/devdata)
#   devstack/down.sh
# Nothing here talks to the real drone or the Pi's database.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$HERE")"
docker image inspect ardupilot-sitl:4.7.1 >/dev/null 2>&1 || {
  echo "Build the simulator first: docker build -t ardupilot-sitl:4.7.1 ../drone-sim"; exit 1; }
docker build -q -t farm-console-dev -f "$HERE/Dockerfile.console" "$HERE" >/dev/null
docker network inspect farmnet >/dev/null 2>&1 || docker network create farmnet >/dev/null
docker rm -f farm-sitl farm-console >/dev/null 2>&1 || true
docker run -d --name farm-sitl --network farmnet -p 5763:5763 -e HOME_LOC="25.3094,55.4861,10,0" ardupilot-sitl:4.7.1 >/dev/null
docker run -d --name farm-console --network farmnet -p 127.0.0.1:8090:8000 -v "$REPO:/app" \
  -e CONSOLE_DEV=1 -e CONSOLE_SIM=1 -e CONSOLE_HOST=0.0.0.0 -e CONSOLE_PORT=8000 \
  -e SIM_MAVLINK_URL=tcp:farm-sitl:5760 -e CONSOLE_DATA=/app/devstack/devdata \
  -e CONSOLE_DEV_IMAGES=/app/devstack/images -e PYTHONUNBUFFERED=1 farm-console-dev >/dev/null
echo "Simulator + console starting: http://localhost:8090  (logs: docker logs -f farm-console)"
