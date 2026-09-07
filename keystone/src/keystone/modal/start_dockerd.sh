#!/bin/bash

# Script to start the Docker daemon in a Modal sandbox.
# There's some Modal-specific setup to do here.

set -euo pipefail -x

# Clean up stale state from previous runs
rm -f /var/run/docker.pid /run/docker/containerd/containerd.pid \
      /var/run/docker/containerd/containerd.pid /var/run/docker.sock

# Remove stale docker0 bridge if it exists (can take time to take effect)
if ip link show docker0 &>/dev/null; then
    ip link delete docker0 || true
    sleep 2
fi

# Find default network device and IP
dev=$(ip route show default | awk '/default/ {print $5}')
if [ -z "$dev" ]; then
    echo "Error: No default device found."
    ip route show
    exit 1
else
    echo "Default device: $dev"
fi
addr=$(ip addr show dev "$dev" | grep -w inet | awk '{print $2}' | cut -d/ -f1)
if [ -z "$addr" ]; then
    echo "Error: No IP address found for device $dev."
    ip addr show dev "$dev"
    exit 1
else
    echo "IP address for $dev: $addr"
fi

# Set up IP forwarding and NAT for container networking
echo 1 > /proc/sys/net/ipv4/ip_forward
iptables-legacy -t nat -A POSTROUTING -o "$dev" -j SNAT --to-source "$addr" -p tcp
iptables-legacy -t nat -A POSTROUTING -o "$dev" -j SNAT --to-source "$addr" -p udp

# gVisor doesn't support nftables yet (https://github.com/google/gvisor/issues/10510)
# Explicitly use iptables-legacy
update-alternatives --set iptables /usr/sbin/iptables-legacy
update-alternatives --set ip6tables /usr/sbin/ip6tables-legacy

# You can add -D to get debug output from dockerd.
exec /usr/bin/dockerd --iptables=false --ip6tables=false
