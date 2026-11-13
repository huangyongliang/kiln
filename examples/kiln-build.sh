#!/bin/sh
# 最小产品构建脚本。运维把它复制到实验室代码仓库的根目录。
# Jenkins 检出提交后在仓库根目录执行本脚本，并要求 kiln-dist/artifact.bin 非空。
# 本脚本不访问 Nexus。不要在日常 CI 里对真实 Jenkins 执行它。
set -eu
mkdir -p kiln-dist
printf 'kiln\n' > kiln-dist/artifact.bin
