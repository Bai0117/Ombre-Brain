# Cloudflare WARP 出口代理（可选）
#
# 为什么需要：有些机房 IP 会被 Google 判成「不支持的地区」，Gemini API 直接回
#   400 "User location is not supported for the API use."
# 这个小容器用 wgcf 注册一个免费 WARP 账号，再用 wireproxy 在用户态跑 WireGuard，
# 对内部网络暴露一个 HTTP 代理（warp:25345）。不改宿主机路由、不需要 NET_ADMIN，
# 不会影响 SSH。只有 OB 容器通过 HTTPS_PROXY 用它，而且 DeepSeek / GitHub 等走 NO_PROXY 直连。
#
# 两个工具都从官方仓库的固定版本源码构建。

FROM golang:1.26-alpine AS build
RUN apk add --no-cache git
ARG WGCF_VERSION=v2.3.1
ARG WIREPROXY_VERSION=v1.1.3
ENV CGO_ENABLED=0
RUN git clone --depth 1 --branch "$WGCF_VERSION" https://github.com/ViRb3/wgcf /src/wgcf \
 && cd /src/wgcf && go build -trimpath -ldflags="-s -w" -o /out/wgcf .
RUN git clone --depth 1 --branch "$WIREPROXY_VERSION" https://github.com/whyvl/wireproxy /src/wireproxy \
 && cd /src/wireproxy && go build -trimpath -ldflags="-s -w" -o /out/wireproxy ./cmd/wireproxy

FROM alpine:3
RUN apk add --no-cache ca-certificates
COPY --from=build /out/wgcf /out/wireproxy /usr/local/bin/
COPY entrypoint.sh /entrypoint.sh
VOLUME ["/data"]
EXPOSE 25345
ENTRYPOINT ["/bin/sh", "/entrypoint.sh"]
