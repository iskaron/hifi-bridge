# Hi-Fi Bridge - developer entry points. Host requirements: Docker with Compose v2 and make. Everything runs in containers.
# Host requirements: Docker Engine >= 24.0 with Compose >= 2.20 and make (docs/DOCKER_REQUIREMENTS.md); `make doctor` checks them.
.PHONY: help ota doctor entry-test demo demo-stop demo-status demo-logs demo-reset demo-trust demo-recover demo-ha demo-ha-stop demo-test test-all

help:
	@echo "make doctor        check Docker/Compose versions, disk, ports and images; changes nothing"
	@echo "make demo          build and start the interactive demo (docs/DEMO.md), print URLs and the setup code"
	@echo "make demo-status   status, URLs, setup code, resource use"
	@echo "make demo-logs     follow the demo logs (SERVICE=hifi-bridge for one service)"
	@echo "make demo-stop     stop the demo (data is kept)"
	@echo "make demo-reset    delete ONLY the demo's own data (asks for confirmation)"
	@echo "make demo-trust    verify the bridge's CA with the owner's trust key and save it for the browser"
	@echo "make demo-recover  forgot the owner password: SD-card recovery (new one-time setup code, settings kept)"
	@echo "make demo-ha       additionally start Home Assistant with the MQTT integration"
	@echo "make demo-test     demo unit tests + full demo stack test (separate ports, own project)"
	@echo "make entry-test     tests of the entry scripts, compose isolation rules and image pins (no daemon needed)"
	@echo "(HB_QUIET=1 make demo prints step lines only, for CI)"
	@echo "make test-all      every test suite (scripts/run_all.sh)"
	@echo "make ota ROOTFS=output/images/rootfs.ext4 COMPATIBLE=hifi-bridge-rpi3 [VERSION=x] [CERT=.. KEY=..]"
	@echo "                   signed RAUC update bundle (development keys in .keys/ unless CERT/KEY are given; docs/OTA.md)"

doctor:
	@scripts/doctor.sh
entry-test:
	@scripts/run_entry_tests.sh
demo:
	@scripts/demo.sh up
demo-stop:
	@scripts/demo.sh stop
demo-status:
	@scripts/demo.sh status
demo-logs:
	@scripts/demo.sh logs $(SERVICE)
demo-reset:
	@scripts/demo.sh reset
demo-trust:
	@scripts/demo.sh trust
demo-recover:
	@scripts/demo.sh recover
demo-ha:
	@scripts/demo.sh ha
demo-ha-stop:
	@scripts/demo.sh ha-stop
demo-test:
	@scripts/run_demo_tests.sh -q && scripts/test_demo.sh
test-all:
	@scripts/run_all.sh
ota:
	@scripts/os_bundle.sh --rootfs "$(ROOTFS)" --compatible "$(COMPATIBLE)" $(if $(VERSION),--version "$(VERSION)") $(if $(OUT),--out "$(OUT)") $(if $(CERT),--cert "$(CERT)" --key "$(KEY)")
