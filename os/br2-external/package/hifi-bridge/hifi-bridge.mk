################################################################################
#
# hifi-bridge (the product's own service; Python stdlib + jeepney)
#
################################################################################

HIFI_BRIDGE_VERSION = $(shell sed -n 's/^__version__ = "\(.*\)"/\1/p' $(BR2_EXTERNAL_HIFI_BRIDGE_PATH)/../../bridge/btbridge/__init__.py)
HIFI_BRIDGE_SITE = $(BR2_EXTERNAL_HIFI_BRIDGE_PATH)/../../bridge
HIFI_BRIDGE_SITE_METHOD = local
HIFI_BRIDGE_LICENSE = AGPL-3.0-only
# bridge/LICENSE is an identical copy of the repository LICENSE (checked by scripts/run_static.sh)
HIFI_BRIDGE_LICENSE_FILES = LICENSE
HIFI_BRIDGE_DEPENDENCIES = python3 python-jeepney

HIFI_BRIDGE_PACKAGING = $(BR2_EXTERNAL_HIFI_BRIDGE_PATH)/../../packaging

define HIFI_BRIDGE_INSTALL_TARGET_CMDS
	mkdir -p $(TARGET_DIR)/usr/lib/hifi-bridge
	rsync -a --delete --exclude '__pycache__' --exclude '*.pyc' --exclude '_generate*.py' \
		$(@D)/btbridge/ $(TARGET_DIR)/usr/lib/hifi-bridge/btbridge/
endef

# Bytecode for the read-only root: Python cannot write __pycache__ there and would recompile every imported module in memory at each
# start. "unchecked-hash" pycs carry no source timestamp (reproducible); source and bytecode always come from the same image.
# host-python3 is the same Python version as the target's (one python3 package in Buildroot).
HIFI_BRIDGE_DEPENDENCIES += host-python3
define HIFI_BRIDGE_COMPILE_PYC
	$(HOST_DIR)/bin/python3 -m compileall -q --invalidation-mode unchecked-hash \
		-s $(TARGET_DIR) -p / $(TARGET_DIR)/usr/lib/hifi-bridge/btbridge
endef
HIFI_BRIDGE_POST_INSTALL_TARGET_HOOKS += HIFI_BRIDGE_COMPILE_PYC

define HIFI_BRIDGE_INSTALL_INIT_SYSTEMD
	$(INSTALL) -D -m 0644 $(HIFI_BRIDGE_PACKAGING)/systemd/hifi-bridge.service \
		$(TARGET_DIR)/usr/lib/systemd/system/hifi-bridge.service
	$(INSTALL) -D -m 0644 $(HIFI_BRIDGE_PACKAGING)/systemd/hifi-bridge-boot.service \
		$(TARGET_DIR)/usr/lib/systemd/system/hifi-bridge-boot.service
endef

define HIFI_BRIDGE_USERS
	hifi-bridge 990 hifi-bridge 990 * /var/lib/hifi-bridge - bluetooth,audio Hi-Fi Bridge service
endef

$(eval $(generic-package))
