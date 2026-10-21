#!/bin/sh
# 首次启动注册免费 WARP 账号并生成 WireGuard 配置（存在 /data，重启复用），
# 然后用 wireproxy 在 0.0.0.0:25345 提供 HTTP 代理（只在 compose 内部网络可达）。
set -eu
cd /data

if [ ! -s wgcf-profile.conf ]; then
  if [ ! -s wgcf-account.toml ]; then
    echo "[warp] 注册 WARP 账号..."
    wgcf register --accept-tos
  fi
  echo "[warp] 生成 WireGuard 配置..."
  wgcf generate --keepalive=25
fi

cat > /tmp/wireproxy.conf <<EOF
WGConfig = /data/wgcf-profile.conf

[http]
BindAddress = 0.0.0.0:25345
EOF

echo "[warp] 启动 HTTP 代理 :25345"
exec wireproxy -c /tmp/wireproxy.conf
