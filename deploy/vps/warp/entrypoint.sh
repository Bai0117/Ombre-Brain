#!/bin/sh
# 首次启动注册免费 WARP 账号并生成 WireGuard 配置（存在 /data，重启复用），
# 然后用 wireproxy 在 0.0.0.0:25345 提供 HTTP 代理（只在 compose 内部网络可达）。
#
# 自检：隧道偶尔会「代理还活着，但 WireGuard 那头已经不通」——这时 Gemini 请求
# 全部被断开，OB 报一串 OB-E001，hold 也会卡到超时，手动 docker restart 才好。
# 所以这里每隔一段时间经自己的代理去访问一次 Google；连续失败到阈值就退出，
# 交给 compose 的 restart: unless-stopped 把容器重新拉起来（等同于手动重启）。
set -eu

DATA_DIR="${WARP_DATA_DIR:-/data}"
PORT="${WARP_PORT:-25345}"
CHECK_URL="${WARP_CHECK_URL:-https://generativelanguage.googleapis.com/}"
CHECK_INTERVAL="${WARP_CHECK_INTERVAL:-60}"   # 秒，两次自检之间
CHECK_GRACE="${WARP_CHECK_GRACE:-20}"         # 秒，启动后先等隧道握手
CHECK_FAILS="${WARP_CHECK_FAILS:-3}"          # 连续失败几次才重启
CHECK_TIMEOUT="${WARP_CHECK_TIMEOUT:-15}"     # 秒，单次自检超时

cd "$DATA_DIR"

if [ ! -s wgcf-profile.conf ]; then
  if [ ! -s wgcf-account.toml ]; then
    echo "[warp] 注册 WARP 账号..."
    wgcf register --accept-tos
  fi
  echo "[warp] 生成 WireGuard 配置..."
  wgcf generate --keepalive=25
fi

cat > /tmp/wireproxy.conf <<EOF
WGConfig = $DATA_DIR/wgcf-profile.conf

[http]
BindAddress = 0.0.0.0:$PORT
EOF

echo "[warp] 启动 HTTP 代理 :$PORT"
wireproxy -c /tmp/wireproxy.conf &
WP=$!

stop() { kill "$WP" 2>/dev/null || true; wait "$WP" 2>/dev/null || true; exit 0; }
trap stop TERM INT

# 可打断的 sleep：docker stop 时 trap 能立刻生效，而不是等一整轮
nap() { sleep "$1" & wait $! || true; }

# 只要拿到任何 HTTP 响应（哪怕 404）就说明隧道是通的；不加 -f。
probe() { curl -s -o /dev/null --max-time "$CHECK_TIMEOUT" -x "http://127.0.0.1:$PORT" "$CHECK_URL"; }

nap "$CHECK_GRACE"
fails=0
while kill -0 "$WP" 2>/dev/null; do
  if probe; then
    if [ "$fails" -gt 0 ]; then echo "[warp] 自检恢复正常"; fi
    fails=0
  else
    fails=$((fails + 1))
    echo "[warp] 自检失败 $fails/$CHECK_FAILS：经隧道连不上 $CHECK_URL"
    if [ "$fails" -ge "$CHECK_FAILS" ]; then
      echo "[warp] 隧道连续 $CHECK_FAILS 次不通，退出让 Docker 重启容器"
      kill "$WP" 2>/dev/null || true
      wait "$WP" 2>/dev/null || true
      exit 1
    fi
  fi
  nap "$CHECK_INTERVAL"
done

# wireproxy 自己挂了：带着它的退出码退出，同样交给 Docker 重启
set +e
wait "$WP"
code=$?
echo "[warp] wireproxy 退出（code=$code）"
exit "$code"
