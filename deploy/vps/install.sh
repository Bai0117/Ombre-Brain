#!/usr/bin/env bash
# ============================================================
# Ombre Brain — VPS 一键安装 / 更新脚本
#
# 在一台全新的 Ubuntu / Debian VPS 上，用 root 运行：
#   curl -fsSL https://raw.githubusercontent.com/Bai0117/ombre-brain/main/deploy/vps/install.sh -o ob.sh && bash ob.sh
#
# 它会：
#   1. 内存不足 2G 时自动加 swap（防止构建镜像时被 OOM 杀掉）
#   2. 安装 Docker
#   3. 把仓库拉到 /opt/ombre-brain（已存在则更新）
#   4. 问你几个问题（Dashboard 密码、API key），写进 deploy/vps/.env（权限 600）
#   5. 没有域名就用 <IP>.sslip.io，Caddy 自动申请 HTTPS 证书
#   6. 构建并启动，做健康检查，最后告诉你连接地址
#
# 重复运行 = 更新：拉最新代码、重建、重启；.env 和记忆数据都会保留。
# 记忆数据在 /opt/ombre-data，和代码分开放。
# ============================================================
set -euo pipefail

REPO_URL="${OMBRE_REPO_URL:-https://github.com/Bai0117/ombre-brain.git}"
REPO_BRANCH="${OMBRE_REPO_BRANCH:-main}"
APP_DIR="${OMBRE_APP_DIR:-/opt/ombre-brain}"
DATA_DIR="${OMBRE_DATA_DIR:-/opt/ombre-data}"
ENV_FILE="$APP_DIR/deploy/vps/.env"
COMPOSE_FILE="$APP_DIR/deploy/vps/docker-compose.vps.yml"
INFO_FILE="/root/ombre-info.txt"

say()  { printf '\n\033[1;35m▶ %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m  ✓ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m  ! %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m  ✗ %s\033[0m\n' "$*"; exit 1; }

ask() {        # ask VAR "提示" [默认值]
  local __var="$1" __prompt="$2" __def="${3:-}" __ans=""
  read -r -p "  $__prompt" __ans < /dev/tty || true
  printf -v "$__var" '%s' "${__ans:-$__def}"
}
ask_secret() { # ask_secret VAR "提示"
  local __var="$1" __prompt="$2" __ans=""
  read -r -s -p "  $__prompt" __ans < /dev/tty || true
  echo
  printf -v "$__var" '%s' "$__ans"
}

envget() {     # envget KEY  → 从 .env 读值（去掉单引号）
  sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1 | sed "s/^'//; s/'\$//"
}
q() {          # q VALUE → 单引号包起来，避免 compose 把 $ 当变量
  case "$1" in *"'"*) die "输入里不能包含单引号 '" ;; esac
  printf "'%s'" "$1"
}

[ "$(id -u)" -eq 0 ] || die "请用 root 运行（或在命令前加 sudo）"
command -v apt-get >/dev/null 2>&1 || die "这个脚本只支持 Ubuntu / Debian 系统，重装系统时请选 Ubuntu 22.04 或 24.04"

echo
echo "  🌙 Ombre Brain 搬家脚本"
echo "  代码目录：$APP_DIR"
echo "  记忆目录：$DATA_DIR"

# ---------- 1. 基础工具 ----------
say "安装基础工具"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git curl ca-certificates openssl >/dev/null
ok "git / curl 就绪"

# ---------- 2. swap ----------
MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
SWAP_MB=$(awk '/SwapTotal/ {print int($2/1024)}' /proc/meminfo)
if [ "$MEM_MB" -lt 2000 ] && [ "$SWAP_MB" -lt 1000 ]; then
  say "内存 ${MEM_MB}MB，加 2G swap 防止构建时内存不够"
  if [ ! -f /swapfile ]; then
    fallocate -l 2G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
  fi
  swapon /swapfile 2>/dev/null || true
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  ok "swap 已开启"
fi

# ---------- 3. Docker ----------
if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  say "安装 Docker（第一次大概要一两分钟）"
  curl -fsSL https://get.docker.com | sh >/dev/null
  systemctl enable --now docker >/dev/null 2>&1 || true
fi
docker compose version >/dev/null 2>&1 || die "Docker Compose 没装好，把上面的报错截图发给 Claude"
ok "Docker 就绪"

# ---------- 4. 代码 ----------
say "获取 Ombre Brain 代码"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch --depth 1 origin "$REPO_BRANCH"
  git -C "$APP_DIR" reset --hard "origin/$REPO_BRANCH"
  ok "已更新到最新版本"
else
  git clone --depth 1 --branch "$REPO_BRANCH" "$REPO_URL" "$APP_DIR"
  ok "已下载到 $APP_DIR"
fi
mkdir -p "$DATA_DIR"

# ---------- 5. 配置 ----------
if [ -f "$ENV_FILE" ]; then
  say "找到已有配置，沿用（想重新配置就删掉 $ENV_FILE 再运行）"
  SITE=$(envget OMBRE_SITE_ADDRESS)
  DASH_PW=$(envget OMBRE_DASHBOARD_PASSWORD)
  [ -n "$SITE" ] || die "$ENV_FILE 里缺少 OMBRE_SITE_ADDRESS"
else
  say "回答几个问题（输入密码和 key 时屏幕上不会显示，正常的）"

  PUBLIC_IP=$(curl -4 -fsS --max-time 10 https://api.ipify.org 2>/dev/null \
           || curl -4 -fsS --max-time 10 https://ifconfig.me 2>/dev/null || true)
  [ -n "$PUBLIC_IP" ] || die "查不到这台机器的公网 IPv4"
  ok "公网 IP：$PUBLIC_IP"

  echo
  echo "  ① 域名"
  ask DOMAIN "有自己的域名就输入（比如 ob.example.com），没有直接回车：" ""
  if [ -n "$DOMAIN" ]; then
    SITE="$DOMAIN"
    warn "记得先把 $DOMAIN 的 A 记录指到 $PUBLIC_IP"
  else
    SITE="${PUBLIC_IP//./-}.sslip.io"
    ok "使用免费地址：$SITE"
  fi

  echo
  echo "  ② Dashboard 密码（以后登录面板、授权 claude.ai 都用它）"
  ask_secret DASH_PW "输入密码，直接回车就自动生成一个："
  if [ -z "$DASH_PW" ]; then
    DASH_PW=$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-20)
    ok "已生成密码（最后会显示给你）"
  fi

  echo
  echo "  ③ 压缩 / 打标用的 LLM API key（hold 要用的那个，Zeabur 环境变量里的 OMBRE_COMPRESS_API_KEY）"
  ask_secret C_KEY "粘贴 key（暂时没有就回车，之后在面板「③ 引擎」里填）："
  ask C_BASE "Base URL（回车 = 不改，用默认 Gemini）：" ""
  ask C_MODEL "模型名（回车 = 不改）：" ""

  echo
  echo "  ④ 向量化 embedding 的 API key"
  ask_secret E_KEY "粘贴 key（回车 = 和上面那个一样）："
  E_KEY="${E_KEY:-$C_KEY}"
  ask E_BASE "Embedding Base URL（回车 = 不改）：" ""
  ask E_MODEL "Embedding 模型名（回车 = 不改）：" ""

  echo
  ask AI_NAME_IN "AI 的名字（信件署名用，回车 = Luar）：" "Luar"

  for _v in "$DASH_PW" "$C_KEY" "$C_BASE" "$C_MODEL" "$E_KEY" "$E_BASE" "$E_MODEL" "$AI_NAME_IN"; do
    case "$_v" in *"'"*) die "输入里不能包含单引号 '，重新运行一次脚本吧" ;; esac
  done

  umask 077
  cat > "$ENV_FILE" <<EOF
# 由 install.sh 生成。包含密钥，不要上传、不要截图发给别人。
OMBRE_SITE_ADDRESS=$(q "$SITE")
OMBRE_HOST_VAULT_DIR=$(q "$DATA_DIR")
OMBRE_DASHBOARD_PASSWORD=$(q "$DASH_PW")
OMBRE_COMPRESS_API_KEY=$(q "$C_KEY")
OMBRE_COMPRESS_BASE_URL=$(q "$C_BASE")
OMBRE_COMPRESS_MODEL=$(q "$C_MODEL")
OMBRE_EMBED_API_KEY=$(q "$E_KEY")
OMBRE_EMBED_BASE_URL=$(q "$E_BASE")
OMBRE_EMBED_MODEL=$(q "$E_MODEL")
AI_NAME=$(q "$AI_NAME_IN")
EOF
  chmod 600 "$ENV_FILE"
  umask 022
  ok "配置已保存（只有 root 能读）"
fi

# ---------- 6. 防火墙 ----------
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
  ufw allow 80/tcp >/dev/null && ufw allow 443/tcp >/dev/null
  ok "防火墙已放行 80 / 443"
fi

# ---------- 7. 构建并启动 ----------
# 可选 WARP 出口：.env 里有 OMBRE_USE_WARP='1' 时启用（Gemini 报地区不支持时用）
if [ "$(envget OMBRE_USE_WARP)" = "1" ]; then
  export COMPOSE_PROFILES=warp
  export OMBRE_EGRESS_PROXY="http://warp:25345"
  mkdir -p /opt/ombre-warp
  ok "已启用 WARP 出口（Gemini 请求经 Cloudflare 转发）"
else
  export COMPOSE_PROFILES=""
  export OMBRE_EGRESS_PROXY=""
fi

say "构建并启动（第一次要几分钟，屏幕会滚很多字，别关窗口）"
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d --build --remove-orphans

say "等 Ombre Brain 醒过来"
HEALTHY=0
for _ in $(seq 1 60); do
  if curl -fsS -m 5 "http://127.0.0.1:${OMBRE_HOST_PORT:-18001}/health" 2>/dev/null | grep -q '"status":"ok"'; then
    HEALTHY=1; break
  fi
  sleep 3
done
if [ "$HEALTHY" -ne 1 ]; then
  docker logs --tail 40 ombre-brain 2>&1 || true
  die "Ombre Brain 没能正常启动。把上面的日志截图发给 Claude"
fi
ok "Ombre Brain 正常运行"

say "等 HTTPS 证书（通常不到一分钟）"
HTTPS_OK=0
for _ in $(seq 1 40); do
  if curl -fsS -m 8 "https://$SITE/health" 2>/dev/null | grep -q '"status":"ok"'; then
    HTTPS_OK=1; break
  fi
  sleep 3
done
if [ "$HTTPS_OK" -eq 1 ]; then
  ok "HTTPS 已就绪"
else
  warn "HTTPS 暂时还没通。可能证书还在申请，或 VPS 后台的防火墙没开 80/443。"
  warn "过两分钟在浏览器打开 https://$SITE/health 看看；还不行就把下面这段日志截图发给 Claude："
  docker logs --tail 20 ombre-caddy 2>&1 || true
fi

cat > "$INFO_FILE" <<EOF
Ombre Brain 搬家信息（$(date '+%Y-%m-%d %H:%M')）
Dashboard 面板：   https://$SITE/
claude.ai 连接器： https://$SITE/mcp
Dashboard 密码：   $DASH_PW
记忆数据目录：     $DATA_DIR
更新 / 重启：      bash $APP_DIR/deploy/vps/install.sh
EOF
chmod 600 "$INFO_FILE"

echo
echo "  ════════════════════════════════════════════"
echo "  🌙 新家建好了"
echo
echo "  Dashboard 面板：   https://$SITE/"
echo "  claude.ai 连接器： https://$SITE/mcp"
echo "  Dashboard 密码：   $DASH_PW"
echo
echo "  这些信息也存在了 $INFO_FILE"
echo "  （想再看一次：cat $INFO_FILE）"
echo
echo "  接下来回去找 Claude，下一步是："
echo "  在面板里选「公网安全模式」→ 从 GitHub 恢复记忆 → 连上 claude.ai"
echo "  ════════════════════════════════════════════"
echo
