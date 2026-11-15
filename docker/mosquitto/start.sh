#!/bin/sh
# Lab-only credentials, created at container start (never shipped in an image or used outside the isolated lab network).
set -e
mosquitto_passwd -b -c /mosquitto/data/passwords bridge lab-only-mqtt-password
chown mosquitto:mosquitto /mosquitto/data/passwords 2>/dev/null || true
chmod 0700 /mosquitto/data/passwords 2>/dev/null || true
exec mosquitto -c /mosquitto/config/mosquitto.conf
