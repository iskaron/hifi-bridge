#!/bin/bash
# Runs INSIDE the disposable Docker-in-Docker sandbox (scripts/test_entry_clean.sh) on a fresh clone of the repository, where NO project image
# exists: the entry points must find missing images, build them in the right order and show progress. Everything below is the transcript.
# Secrets of the throw-away demo (setup code, MQTT password) are redacted by the caller.
cd /clone/repo || exit 1
rc=0
t() { date +%H:%M:%S; }
echo "### $(t) docker and compose versions"
docker version --format 'engine {{.Server.Version}} (API {{.Server.APIVersion}}), client {{.Client.Version}}'
docker compose version
echo "### $(t) images before: only the sandbox's own (none of the project's)"
docker image ls --format '{{.Repository}}:{{.Tag}}' | grep -E '^(btbridge-|hifi-demo|lmscommunity|eclipse-mosquitto)' || echo "(none)"
echo "### $(t) decoy: a fake Lyrion on the Docker host (all addresses, TCP 9000 and 3483) - nothing in the demo may reach it"
for p in 9000 3483; do (while true; do nc -l -p $p </dev/null >/dev/null 2>&1; done) & done
echo "### $(t) make demo   (first run on a clean machine: builds hifi-demo:dev, pulls the pinned Lyrion and Mosquitto images)"
s=$(date +%s); make demo; r=$?; echo "make demo: exit $r after $(( $(date +%s) - s )) s"; [ $r = 0 ] || rc=1
echo "### $(t) make demo-status"
make demo-status; [ $? = 0 ] || rc=1
echo "### $(t) the published ports answer (bridge redirects to the setup wizard, console, Lyrion)"
for pp in 18080 18100 19000; do printf 'port %s -> ' $pp; curl -s -o /dev/null -m 10 -w '%{http_code} %{redirect_url}\n' "http://127.0.0.1:$pp/" || rc=1; done
echo "### $(t) runtime isolation check against the real demo (decoy listening on the host)"
scripts/check_isolation.sh hifi-demo --allow-edge gateway; [ $? = 0 ] || rc=1
echo "### $(t) make demo-stop"
make demo-stop; [ $? = 0 ] || rc=1
docker ps --format '{{.Names}} {{.Status}}'
echo "### $(t) scripts/hwkit_bundle.sh   (btbridge-lab:dev is missing here: it must be built first, then btbridge-hwkit:dev)"
s=$(date +%s); scripts/hwkit_bundle.sh; r=$?; echo "hwkit_bundle: exit $r after $(( $(date +%s) - s )) s"; [ $r = 0 ] || rc=1
ls -la dist | head -8
echo "### $(t) images after"
docker image ls --format '{{.Repository}}:{{.Tag}} {{.Size}}' | grep -E '^(btbridge-|hifi-demo|lmscommunity|eclipse-mosquitto)'
pkill nc 2>/dev/null
echo "CLEAN-ENVIRONMENT RESULT: $([ $rc = 0 ] && echo PASS || echo FAIL)"
exit $rc
