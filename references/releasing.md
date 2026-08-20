# 发布 runbook

本文只供仓库维护者发布正式版本。它不替代 `README.md` 中的候选归档门禁；任一断言、签名、CI、下载或字节比较失败都必须停止发布。禁止强推、移动既有 tag，或在发布后替换资产。

## 身份与边界

- 发布账号：`longlannet`，必须对 `longlannet/google-search` 具有 admin 权限。
- 当前维护者签名：GitHub 用户 `xxvcc` 注册的 OpenPGP primary fingerprint `C678256ACBFC6491BF5076655F3AE24999921FFC`。
- 这表示 commit、tag 和 checksum 由 xxvcc key 签名，push 和 GitHub Release 由 longlannet 发布。Release notes 必须明确披露这两个身份。
- GitHub immutable release attestation 绑定 published release、tag、commit 和资产字节。它不是 SLSA build provenance；源码 tar 的构建来源还依赖 README 的 commit-bound audited archive 门禁以及下面的签名 checksum。

## 可信发布 shell

必须从已经确认可信、由 root 运行的现有 Bash 会话执行下面的 launcher。它只用 shell builtin 和绝对系统路径清除可能影响子进程的 Bash/loader 环境，再用空环境替换当前 shell；`env -i` 同时清除 exported functions、ambient `GIT_*`、`GH_*`、`GITHUB_*`、代理和语言运行时变量。它不能回溯撤销当前 shell 启动前已经发生的动态加载器影响；若现有会话的来源不可信，必须停止发布并重新建立可信会话。

```bash
builtin set +x
builtin unset BASH_ENV ENV CDPATH GLOBIGNORE
for variable in "${!LD_@}"; do
  builtin unset -v "$variable"
done
builtin unset variable GLIBC_TUNABLES GCONV_PATH
builtin exec /usr/bin/env -i \
  HOME=/root USER=root LOGNAME=root PATH=/usr/bin:/bin LC_ALL=C \
  /bin/bash --noprofile --norc -p
```

在新 shell 中设置本次版本和受控环境，并拒绝不规范输入：

```bash
set -euo pipefail
umask 077
IFS=$' \t\n'
PATH=/usr/bin:/bin
TMPDIR=/tmp
TMP=/tmp
TEMP=/tmp
LC_ALL=C
GIT_CONFIG_NOSYSTEM=1
GIT_CONFIG_GLOBAL=/dev/null
GIT_ATTR_NOSYSTEM=1
GIT_NO_REPLACE_OBJECTS=1
GIT_NO_LAZY_FETCH=1
GIT_OPTIONAL_LOCKS=0
GIT_TERMINAL_PROMPT=0
GH_PROMPT_DISABLED=1
PAGER='cat'
GIT_PAGER='cat'
GH_PAGER='cat'
export PATH TMPDIR TMP TEMP LC_ALL
export GIT_CONFIG_NOSYSTEM GIT_CONFIG_GLOBAL GIT_ATTR_NOSYSTEM GIT_NO_REPLACE_OBJECTS
export GIT_NO_LAZY_FETCH GIT_OPTIONAL_LOCKS GIT_TERMINAL_PROMPT
export GH_PROMPT_DISABLED PAGER GIT_PAGER GH_PAGER
unset SERPER_API_KEY SERPER_API_KEYS SERPER_DEBUG_RR TAR_OPTIONS
release_tmp_directory_is_safe() {
  local directory="$1"
  local canonical
  canonical="$(readlink -m -- "$directory")" || return 1
  [ "$canonical" = "$directory" ] || return 1
  [ -d "$directory" ] && [ ! -L "$directory" ] || return 1
  [ "$(stat -c '%u:%a' -- "$directory")" = '0:1777' ]
}
release_tmp_directory_is_safe /tmp
REPOSITORY=longlannet/google-search
REMOTE_URL="https://github.com/$REPOSITORY.git"
VERSION=v2.0.0
SIGNING_FINGERPRINT=C678256ACBFC6491BF5076655F3AE24999921FFC
[[ "$VERSION" =~ ^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]
[[ "$SIGNING_FINGERPRINT" =~ ^[0-9A-F]{40}$ ]]
COMMIT=
PUBLICATION_LOCK_TARGET="/tmp/google-search-${VERSION}.publication.lock"
PUBLICATION_LOCK=
CANARY=
STAGE=
VERIFY=
PUBLIC=
FRESH_KEYRING=
AUDITED_DIRECTORY=
ORIGINAL_GH_ACCOUNT=
GH_ACCOUNT_SWITCHED=0
set_release_signal_traps() {
  trap 'exit 129' HUP
  trap 'exit 130' INT
  trap 'exit 143' TERM
}
assert_git_local_config_safe() {
  local actual expected git_owner git_mode config_owner config_mode config_links
  local metadata_issue repository_root unsafe_path
  repository_root="$(pwd -P)" || return 1
  [ -d .git ] && [ ! -L .git ] || return 1
  [ "$(/usr/bin/readlink -m -- "$repository_root/.git")" = "$repository_root/.git" ] || return 1
  IFS=: read -r git_owner git_mode < <(stat -c '%u:%a' -- .git) || return 1
  [ "$git_owner" = "$(id -u)" ] || return 1
  case "$git_mode" in ''|*[!0-7]*) return 1 ;; esac
  [ $((8#$git_mode & 0022)) -eq 0 ] || return 1
  metadata_issue="$(
    /usr/bin/find -P .git -xdev \
      \( ! -uid "$(id -u)" -o \
        \( -type d -perm /022 \) -o \
        \( -type f \( -perm /022 -o -links +1 \) \) -o \
        \( ! -type d ! -type f \) \) \
      -print -quit
  )" || return 1
  [ -z "$metadata_issue" ] || return 1
  for unsafe_path in \
    .git/commondir \
    .git/config.worktree \
    .git/info/grafts \
    .git/objects/info/alternates \
    .git/objects/info/http-alternates; do
    [ ! -e "$unsafe_path" ] && [ ! -L "$unsafe_path" ] || return 1
  done
  [ -f .git/config ] && [ ! -L .git/config ] || return 1
  IFS=: read -r config_owner config_mode config_links < <(
    stat -c '%u:%a:%h' -- .git/config
  ) || return 1
  [ "$config_owner" = "$(id -u)" ] && [ "$config_links" = 1 ] || return 1
  case "$config_mode" in ''|*[!0-7]*) return 1 ;; esac
  [ $((8#$config_mode & 0022)) -eq 0 ] || return 1
  actual="$(
    /usr/bin/git --no-pager config --local --no-includes --list
  )" || return 1
  expected="$(printf '%s\n' \
    'core.repositoryformatversion=0' \
    'core.filemode=true' \
    'core.bare=false' \
    'core.logallrefupdates=true' \
    "remote.origin.url=$REMOTE_URL" \
    'remote.origin.fetch=+refs/heads/*:refs/remotes/origin/*' \
    'branch.main.remote=origin' \
    'branch.main.merge=refs/heads/main')"
  [ "$actual" = "$expected" ]
}
trusted_git() {
  assert_git_local_config_safe || return 1
  /usr/bin/git \
    -c core.alternateRefsCommand=/usr/bin/true \
    -c core.attributesFile=/dev/null \
    -c core.excludesFile=/dev/null \
    -c core.fsmonitor=false \
    -c core.hooksPath=/dev/null \
    -c core.untrackedCache=false \
    -c gc.auto=0 \
    -c gpg.format=openpgp \
    -c gpg.program=/usr/bin/gpg \
    -c gpg.openpgp.program=/usr/bin/gpg \
    -c maintenance.auto=false \
    -c user.name=XXV.CC \
    -c user.email=github@xxv.cc \
    "$@"
}
trusted_remote_git() {
  assert_git_transport_config_safe || return 1
  trusted_git \
    -c protocol.allow=never \
    -c protocol.https.allow=always \
    -c credential.helper= \
    -c 'credential.https://github.com.helper=!/usr/bin/gh auth git-credential' \
    -c http.extraHeader= \
    -c fetch.prune=false \
    -c fetch.pruneTags=false \
    -c fetch.recurseSubmodules=false \
    -c fetch.writeCommitGraph=false \
    -c push.followTags=false \
    -c push.gpgSign=false \
    -c push.pushOption= \
    -c push.recurseSubmodules=no \
    -c submodule.recurse=false \
    "$@"
}
assert_git_transport_config_safe() {
  local http_config rewrites status
  if http_config="$(trusted_git config --get-regexp '^http\.')"; then
    [ -z "$http_config" ] || return 1
  else
    status=$?
    [ "$status" -eq 1 ] || return 1
  fi
  if rewrites="$(
    trusted_git config --get-regexp '^url\..*\.(insteadof|pushinsteadof)$'
  )"; then
    [ -z "$rewrites" ] || return 1
  else
    status=$?
    [ "$status" -eq 1 ] || return 1
  fi
  [ "$(trusted_git remote get-url origin)" = "$REMOTE_URL" ]
  [ "$(trusted_git remote get-url --push origin)" = "$REMOTE_URL" ]
}
COMMIT="$(trusted_git rev-parse --verify 'HEAD^{commit}')"
[[ "$COMMIT" =~ ^[0-9a-f]{40}$ ]]
[ "$(trusted_git rev-parse --show-toplevel)" = "$(pwd -P)" ]
[ "$(trusted_git rev-parse --git-dir)" = .git ]
[ "$(trusted_git rev-parse --absolute-git-dir)" = "$(pwd -P)/.git" ]
[ "$(trusted_git rev-parse --path-format=absolute --git-common-dir)" = "$(pwd -P)/.git" ]
[ "$(trusted_git symbolic-ref --quiet --short HEAD)" = main ]
assert_tag_name_unclaimed() {
  local status remote_tags
  if trusted_git show-ref --verify --quiet "refs/tags/$VERSION"; then
    printf '%s\n' 'local release tag already exists' >&2
    return 1
  else
    status=$?
  fi
  [ "$status" -eq 1 ] || return 1
  remote_tags="$(
    trusted_remote_git ls-remote --tags "$REMOTE_URL" \
      "refs/tags/$VERSION" "refs/tags/$VERSION^{}"
  )" || return 1
  [ -z "$remote_tags" ]
}
assert_release_name_unclaimed() {
  local response status http_status
  if response="$(
    gh api --include "repos/$REPOSITORY/releases/tags/$VERSION" 2>&1
  )"; then
    printf '%s\n' 'release or draft already exists' >&2
    return 1
  else
    status=$?
  fi
  [ "$status" -eq 1 ] || return 1
  http_status="$(
    printf '%s\n' "$response" |
      awk 'NR == 1 && $1 ~ /^HTTP\/[0-9.]+$/ && $2 == "404" {print $2}'
  )"
  [ "$http_status" = 404 ]
}
gpg_status_is_acceptable() {
  local status="$1"
  printf '%s\n' "$status" | awk -v expected="$SIGNING_FINGERPRINT" '
    $1 == "[GNUPG:]" {
      if ($2 ~ /^(BADSIG|ERRSIG|EXPSIG|EXPKEYSIG|REVKEYSIG|KEYEXPIRED|KEYREVOKED|SIGEXPIRED)$/) {
        forbidden = 1
      }
      if ($2 == "VALIDSIG") {
        valid += 1
        primary = (length($NF) == 40 ? $NF : $3)
        if (primary != expected || $10 != 8) invalid = 1
      }
    }
    END { exit !(valid == 1 && forbidden == 0 && invalid == 0) }
  '
}
verify_git_commit_signature() {
  local object="$1"
  local status
  private_gnupg_home_is_safe "$FRESH_KEYRING" || return 1
  status="$(GNUPGHOME="$FRESH_KEYRING" trusted_git verify-commit --raw "$object" 2>&1)" || return 1
  gpg_status_is_acceptable "$status"
}
verify_git_tag_signature() {
  local object="$1"
  local status
  private_gnupg_home_is_safe "$FRESH_KEYRING" || return 1
  status="$(GNUPGHOME="$FRESH_KEYRING" trusted_git verify-tag --raw "$object" 2>&1)" || return 1
  gpg_status_is_acceptable "$status"
}
verify_checksum_signature() {
  local directory="$1"
  local status
  private_gnupg_home_is_safe "$FRESH_KEYRING" || return 1
  status="$(cd "$directory" && GNUPGHOME="$FRESH_KEYRING" \
    /usr/bin/gpg --batch --status-fd=1 --verify SHA256SUMS.asc SHA256SUMS 2>/dev/null)" || return 1
  gpg_status_is_acceptable "$status"
}
private_release_directory_path_is_valid() {
  local directory="$1"
  [[ "$directory" =~ ^/tmp/google-search-release(-(assets|verify|public|gnupg|canary))?\.[A-Za-z0-9]{8}$ ]]
}
private_gnupg_home_is_safe() {
  local directory="$1"
  local canonical
  [[ "$directory" =~ ^/tmp/google-search-release-gnupg\.[A-Za-z0-9]{8}$ ]] || return 1
  canonical="$(readlink -m -- "$directory")" || return 1
  [ "$canonical" = "$directory" ] || return 1
  [ -d "$directory" ] && [ ! -L "$directory" ] || return 1
  [ "$(stat -c '%u:%a' -- "$directory")" = "$(id -u):700" ]
}
private_gpg_socket_directory_is_safe() {
  local directory="$1"
  local home="$2"
  local canonical
  canonical="$(readlink -m -- "$directory")" || return 1
  [ "$canonical" = "$directory" ] || return 1
  if [ "$directory" = "$home" ]; then
    private_gnupg_home_is_safe "$home"
    return
  fi
  [[ "$directory" =~ ^/run/user/([0-9]+)/gnupg/d\.[A-Za-z0-9]{24}$ ]] || return 1
  [ "${BASH_REMATCH[1]}" = "$(id -u)" ] || return 1
  if [ ! -e "$directory" ] && [ ! -L "$directory" ]; then
    return 0
  fi
  [ -d "$directory" ] && [ ! -L "$directory" ] || return 1
  [ "$(stat -c '%u:%a' -- "$directory")" = "$(id -u):700" ]
}
stop_private_gpg_agent() {
  local directory="$1"
  local socket_directory agent_socket entry attempt
  private_gnupg_home_is_safe "$directory" || return 1
  socket_directory="$(
    /usr/bin/timeout --signal=TERM --kill-after=5s 30s \
      /usr/bin/gpgconf --homedir "$directory" --list-dirs socketdir
  )" || return 1
  agent_socket="$(
    /usr/bin/timeout --signal=TERM --kill-after=5s 30s \
      /usr/bin/gpgconf --homedir "$directory" --list-dirs agent-socket
  )" || return 1
  private_gpg_socket_directory_is_safe "$socket_directory" "$directory" || return 1
  [ "$agent_socket" = "$socket_directory/S.gpg-agent" ] || return 1
  /usr/bin/timeout --signal=TERM --kill-after=5s 30s \
    /usr/bin/gpgconf --homedir "$directory" --kill all || return 1
  for ((attempt = 0; attempt < 50; attempt += 1)); do
    entry=
    if [ -e "$agent_socket" ] || [ -L "$agent_socket" ]; then
      entry="$agent_socket"
    elif [ "$socket_directory" != "$directory" ] && \
      { [ -e "$socket_directory" ] || [ -L "$socket_directory" ]; }; then
      entry="$(/usr/bin/find -P "$socket_directory" -mindepth 1 -maxdepth 1 -print -quit)" || return 1
    fi
    [ -z "$entry" ] && break
    /usr/bin/sleep 0.1
  done
  [ -z "$entry" ] || return 1
  if [ "$socket_directory" != "$directory" ]; then
    /usr/bin/timeout --signal=TERM --kill-after=5s 30s \
      /usr/bin/gpgconf --homedir "$directory" --remove-socketdir || return 1
    if [ -e "$socket_directory" ] || [ -L "$socket_directory" ]; then
      private_gpg_socket_directory_is_safe "$socket_directory" "$directory" || return 1
      /usr/bin/rmdir -- "$socket_directory" || return 1
    fi
    [ ! -e "$socket_directory" ] && [ ! -L "$socket_directory" ] || return 1
  fi
}
remove_private_release_directory() {
  local directory="$1"
  local canonical
  private_release_directory_path_is_valid "$directory" || return 1
  canonical="$(readlink -m -- "$directory")" || return 1
  [ "$canonical" = "$directory" ] || return 1
  [ -d "$directory" ] && [ ! -L "$directory" ] || return 1
  [ "$(stat -c %u -- "$directory")" = "$(id -u)" ] || return 1
  find -P "$directory" -xdev -depth -delete || return 1
  [ ! -e "$directory" ] && [ ! -L "$directory" ] || return 1
}
cleanup_release_state() {
  status=$?
  trap - EXIT
  trap '' HUP INT TERM
  cleanup_status=0
  if [ -n "${FRESH_KEYRING:-}" ]; then
    if stop_private_gpg_agent "$FRESH_KEYRING"; then
      remove_private_release_directory "$FRESH_KEYRING" || cleanup_status=1
    else
      cleanup_status=1
    fi
  fi
  for directory in "${PUBLIC:-}" "${VERIFY:-}" "${STAGE:-}" \
    "${CANARY:-}" "${AUDITED_DIRECTORY:-}"; do
    [ -z "$directory" ] || remove_private_release_directory "$directory" || cleanup_status=1
  done
  if [ -n "${PUBLICATION_LOCK:-}" ]; then
    if [ "$PUBLICATION_LOCK" = "$PUBLICATION_LOCK_TARGET" ]; then
      rmdir -- "$PUBLICATION_LOCK" >/dev/null 2>&1 || cleanup_status=1
    else
      cleanup_status=1
    fi
  fi
  if [ "${GH_ACCOUNT_SWITCHED:-0}" -eq 1 ] && [ -n "${ORIGINAL_GH_ACCOUNT:-}" ]; then
    if timeout --signal=TERM --kill-after=5s 30s \
      gh auth switch --hostname github.com --user "$ORIGINAL_GH_ACCOUNT" >/dev/null &&
      [ "$(timeout --signal=TERM --kill-after=5s 30s gh api user --jq .login)" = \
        "$ORIGINAL_GH_ACCOUNT" ]; then
      GH_ACCOUNT_SWITCHED=0
    else
      cleanup_status=1
    fi
  fi
  if [ "$status" -eq 0 ] && [ "$cleanup_status" -ne 0 ]; then
    status=1
  fi
  exit "$status"
}
trap cleanup_release_state EXIT
trap '' HUP INT TERM
if mkdir -m 700 -- "$PUBLICATION_LOCK_TARGET"; then
  PUBLICATION_LOCK="$PUBLICATION_LOCK_TARGET"
else
  lock_status=$?
  trap 'exit 129' HUP
  trap 'exit 130' INT
  trap 'exit 143' TERM
  exit "$lock_status"
fi
set_release_signal_traps
[ "$(stat -c '%u:%a' -- "$PUBLICATION_LOCK")" = "$(id -u):700" ]
```

这个 `mkdir` lock 只防止同一主机上的并行发布，不是跨主机协调器；残留 lock 必须人工确认无活动发布后处理，不能自动抢占。Git guard 会在每次调用前遍历 `.git`：只接受当前 uid 所有、不可由 group/other 写入的普通目录和单链接普通文件，并拒绝 `commondir`、worktree config、legacy `info/grafts`、object alternates、symlink 与特殊文件；因此共享 clone、linked worktree 或重定向 Git 元数据的 checkout 不能用于正式发布。

## Signed commit 与 main CI

先从签名者当前权威渠道重新取得 public key，确认其中包含最新撤销与过期状态；不得使用历史缓存、旧导出或仅从本机默认 keyring 再导出的副本。然后只 stage 已逐项审阅的文件，确认 staged manifest 后显式签名；不要使用会把未知文件一并纳入的发布别名或脚本：

```bash
TRUSTED_PUBLIC_KEY="${TRUSTED_PUBLIC_KEY:?set an absolute trusted public-key path}"
case "$TRUSTED_PUBLIC_KEY" in /*) ;; *) false ;; esac
[ -f "$TRUSTED_PUBLIC_KEY" ] && [ ! -L "$TRUSTED_PUBLIC_KEY" ]
FRESH_KEYRING="$(mktemp -d /tmp/google-search-release-gnupg.XXXXXXXX)"
chmod 700 "$FRESH_KEYRING"
private_gnupg_home_is_safe "$FRESH_KEYRING"
GNUPGHOME="$FRESH_KEYRING" /usr/bin/gpg --batch --import "$TRUSTED_PUBLIC_KEY"
private_gnupg_home_is_safe "$FRESH_KEYRING"
IMPORTED_PRIMARY_FINGERPRINTS="$(
  GNUPGHOME="$FRESH_KEYRING" /usr/bin/gpg --batch --with-colons --list-keys --fingerprint |
    awk -F: '$1 == "pub" {want = 1; next} want && $1 == "fpr" {print $10; want = 0}'
)"
[ "$IMPORTED_PRIMARY_FINGERPRINTS" = "$SIGNING_FINGERPRINT" ]
trusted_git diff --cached --check --no-ext-diff --no-textconv
trusted_git diff --cached --name-status --no-ext-diff --no-textconv
[ "$(trusted_git config --get user.name)" = XXV.CC ]
[ "$(trusted_git config --get user.email)" = github@xxv.cc ]
CREDENTIAL_HELPERS="$(trusted_remote_git config --get-all credential.https://github.com.helper | awk 'NF')"
[ "$CREDENTIAL_HELPERS" = '!/usr/bin/gh auth git-credential' ]
SECRET_PRIMARY_FINGERPRINT="$(/usr/bin/gpg --batch --with-colons --list-secret-keys \
  "$SIGNING_FINGERPRINT" | awk -F: '$1 == "fpr" {print $10; exit}')"
[ "$SECRET_PRIMARY_FINGERPRINT" = "$SIGNING_FINGERPRINT" ]
ORIGINAL_GH_ACCOUNT="$(timeout --signal=TERM --kill-after=5s 30s gh api user --jq .login)"
[ "$ORIGINAL_GH_ACCOUNT" = xxvcc ]
trusted_git commit -S"$SIGNING_FINGERPRINT" -m "release: google-search ${VERSION}"
COMMIT="$(trusted_git rev-parse --verify 'HEAD^{commit}')"
[[ "$COMMIT" =~ ^[0-9a-f]{40}$ ]]
verify_git_commit_signature "$COMMIT"
```

上述 helper 只接受恰好一个 `VALIDSIG`，要求其 primary fingerprint 精确等于完整预期值且摘要算法为 SHA-256，并拒绝 bad/error、过期或撤销的签名与 key 状态。在任何远端写入前，先在 root-owned 私有 fresh checkout 中执行 README 的完整候选配方，把成功时打印的绝对路径赋给 `AUDITED_ARCHIVE`。然后从这个精确归档执行一次全新依赖安装、真实 Serper smoke 和 full selfcheck；canary 只在私有目录中短暂复制受保护 key，quiet 模式不保留结果文件，退出 trap 会在失败时同样清理 key、venv 与目录。真实检查可能计费。

```bash
AUDITED_ARCHIVE="${AUDITED_ARCHIVE:?set the path printed by the audited recipe}"
[[ "$AUDITED_ARCHIVE" =~ ^/tmp/google-search-release\.[A-Za-z0-9]{8}/google-search-$COMMIT\.tar$ ]]
[ "$(readlink -m -- "$AUDITED_ARCHIVE")" = "$AUDITED_ARCHIVE" ]
[ "${AUDITED_ARCHIVE##*/}" = "google-search-${COMMIT}.tar" ]
[ -f "$AUDITED_ARCHIVE" ] && [ ! -L "$AUDITED_ARCHIVE" ]
AUDITED_DIRECTORY="${AUDITED_ARCHIVE%/*}"

SERPER_CONFIG_SOURCE="${SERPER_CONFIG_SOURCE:?set an absolute protected Serper config path}"
case "$SERPER_CONFIG_SOURCE" in /*) ;; *) false ;; esac
[ -f "$SERPER_CONFIG_SOURCE" ] && [ ! -L "$SERPER_CONFIG_SOURCE" ]
[ "$(stat -c '%u:%a:%h' -- "$SERPER_CONFIG_SOURCE")" = "$(id -u):600:1" ]
SERPER_CONFIG_METADATA="$(stat -c '%d:%i:%u:%g:%a:%h:%s:%y:%z' -- "$SERPER_CONFIG_SOURCE")"
CANARY="$(mktemp -d /tmp/google-search-release-canary.XXXXXXXX)"
[ "$(stat -c '%u:%a' -- "$CANARY")" = "$(id -u):700" ]
tar --extract --file "$AUDITED_ARCHIVE" --directory "$CANARY"
[ "$SERPER_CONFIG_METADATA" = \
  "$(stat -c '%d:%i:%u:%g:%a:%h:%s:%y:%z' -- "$SERPER_CONFIG_SOURCE")" ]
install -m 600 -- "$SERPER_CONFIG_SOURCE" "$CANARY/config/serper.env"
cmp -- "$SERPER_CONFIG_SOURCE" "$CANARY/config/serper.env"
[ "$SERPER_CONFIG_METADATA" = \
  "$(stat -c '%d:%i:%u:%g:%a:%h:%s:%y:%z' -- "$SERPER_CONFIG_SOURCE")" ]
[ "$(stat -c '%u:%a:%h' -- "$CANARY/config/serper.env")" = "$(id -u):600:1" ]
(
  cd "$CANARY"
  /bin/bash -p scripts/install.sh --venv --install-dependencies \
    --smoke-test --full-check --quiet
)
remove_private_release_directory "$CANARY"
CANARY=
```

候选与真实 canary 全部通过后，确认远端 main 是当前提交的祖先，再做普通 fast-forward push。`test.yml` 的 `timeout-minutes` 必须保持 90；等待它对这个精确 commit 的 Python 3.10、3.11、3.12、3.13、3.14 五个 job 全部 success，不能用旧 run 或其他 commit 的绿色状态代替。

```bash
trap '' HUP INT TERM
GH_ACCOUNT_SWITCHED=1
if timeout --signal=TERM --kill-after=5s 30s \
  gh auth switch --hostname github.com --user longlannet; then
  :
else
  switch_status=$?
  set_release_signal_traps
  exit "$switch_status"
fi
set_release_signal_traps
[ "$(gh api user --jq .login)" = longlannet ]
[ "$(gh api "repos/$REPOSITORY" --jq '.permissions.admin')" = true ]
[ -z "$(trusted_git status --porcelain=v1 --untracked-files=all)" ]
assert_git_transport_config_safe
assert_tag_name_unclaimed
assert_release_name_unclaimed
trusted_remote_git fetch --no-tags --no-prune --recurse-submodules=no \
  --no-write-fetch-head --no-write-commit-graph --no-auto-maintenance "$REMOTE_URL" \
  refs/heads/main:refs/remotes/origin/main
REMOTE_MAIN="$(trusted_remote_git ls-remote --exit-code "$REMOTE_URL" \
  refs/heads/main | awk 'NR == 1 {print $1}')"
[[ "$REMOTE_MAIN" =~ ^[0-9a-f]{40}$ ]]
[ "$(trusted_git rev-parse --verify refs/remotes/origin/main)" = "$REMOTE_MAIN" ]
trusted_git merge-base --is-ancestor "$REMOTE_MAIN" "$COMMIT"
trusted_remote_git push --atomic --no-follow-tags --signed=no --recurse-submodules=no \
  "$REMOTE_URL" "$COMMIT:refs/heads/main"
[ "$(trusted_remote_git ls-remote --exit-code "$REMOTE_URL" \
  refs/heads/main | awk 'NR == 1 {print $1}')" = "$COMMIT" ]

assert_matrix_run_id() {
  local run_id="$1"
  local run_sha run_status run_conclusion expected_jobs actual_jobs
  IFS='|' read -r run_sha run_status run_conclusion < <(
    gh run view "$run_id" --repo "$REPOSITORY" \
      --json headSha,status,conclusion --jq '[.headSha,.status,.conclusion] | join("|")'
  )
  [ "$run_sha" = "$COMMIT" ]
  [ "$run_status" = completed ]
  [ "$run_conclusion" = success ]
  expected_jobs="$(printf 'Python %s\n' 3.10 3.11 3.12 3.13 3.14 | sort)"
  actual_jobs="$(
    gh run view "$run_id" --repo "$REPOSITORY" --json jobs \
      --jq '[.jobs[] | select(.conclusion == "success") | .name] | sort | join("\n")'
  )"
  [ "$actual_jobs" = "$expected_jobs" ]
}

wait_for_matrix_run() {
  local branch="$1"
  local run_id='' deadline
  deadline=$((SECONDS + 300))
  while [ -z "$run_id" ]; do
    run_id="$(
      gh run list --repo "$REPOSITORY" --workflow test.yml --branch "$branch" \
        --commit "$COMMIT" --event push --limit 20 \
        --json databaseId --jq '.[0].databaseId // empty'
    )"
    [ -n "$run_id" ] && break
    [ "$SECONDS" -lt "$deadline" ]
    sleep 5
  done
  [ -n "$run_id" ]
  gh run watch "$run_id" --repo "$REPOSITORY" --exit-status >/dev/null
  assert_matrix_run_id "$run_id"
  printf '%s\n' "$run_id"
}

MAIN_RUN_ID="$(wait_for_matrix_run main)"
```

记录 `MAIN_RUN_ID`；后续不能用旧 run 或其他 commit 的绿色状态代替。

## 候选资产与签名 tag

复用任何远端写入前已经通过完整门禁和真实 canary 的 `AUDITED_ARCHIVE`；版本化副本必须逐字节相同：

```bash
[ -f "$AUDITED_ARCHIVE" ] && [ ! -L "$AUDITED_ARCHIVE" ]
STAGE="$(mktemp -d /tmp/google-search-release-assets.XXXXXXXX)"
VERIFY="$(mktemp -d /tmp/google-search-release-verify.XXXXXXXX)"
ASSET="google-search-${VERSION}.tar"
install -m 600 -- "$AUDITED_ARCHIVE" "$STAGE/$ASSET"
cmp -- "$AUDITED_ARCHIVE" "$STAGE/$ASSET"
(
  cd "$STAGE"
  sha256sum "$ASSET" >SHA256SUMS
)
/usr/bin/gpg --batch --yes --armor --digest-algo SHA256 --detach-sign \
  --local-user "$SIGNING_FINGERPRINT" \
  --output "$STAGE/SHA256SUMS.asc" "$STAGE/SHA256SUMS"
```

复用 commit 前已经从当前权威渠道导入、核对过唯一完整 primary fingerprint 且包含最新撤销/过期状态的全新 `GNUPGHOME`，验证 `SHA256SUMS.asc` 和 tar hash。历史缓存或旧导出即使 fingerprint 相同也不能证明当前未撤销；仅从同一主机导出再导入 key 只能检查文件自洽，不能建立签名者身份：

```bash
(
  cd "$STAGE"
  sha256sum --check --strict SHA256SUMS
)
verify_checksum_signature "$STAGE"
```

再次确认本地、远端均不存在这个 tag，且 publisher 可见范围内没有同名 Release 或 draft；然后创建 signed annotated tag，验证签名及 peel，并将 main 与 tag 放在同一个 atomic push 中。再次等待 tag push 对精确 commit 的五个矩阵 job 全绿。

从下面第二次 tag/release precheck 开始，必须先在所有 publisher/admin 之间取得并保持跨主机独占 publication window；该窗口贯穿 tag push 与 CI、immutable 设置、draft 创建与回下载验证以及 publish，直到 immutable 终验完成。在此期间禁止其他主机、账号或 admin 会话修改 main/tag refs、draft notes/assets 或 Release。本机 publication lock 不能提供这种跨主机协调：

```bash
assert_tag_name_unclaimed
assert_release_name_unclaimed
trusted_git tag -s -u "$SIGNING_FINGERPRINT" \
  -m "google-search ${VERSION}" "$VERSION" "$COMMIT"
verify_git_tag_signature "$VERSION"
[ "$(trusted_git rev-parse --verify "$VERSION^{commit}")" = "$COMMIT" ]
LOCAL_TAG_OBJECT="$(trusted_git rev-parse --verify "refs/tags/$VERSION")"
[[ "$LOCAL_TAG_OBJECT" =~ ^[0-9a-f]{40}$ ]]
REMOTE_MAIN_BEFORE_TAG="$(trusted_remote_git ls-remote --exit-code "$REMOTE_URL" \
  refs/heads/main | awk 'NR == 1 {print $1}')"
[ "$REMOTE_MAIN_BEFORE_TAG" = "$COMMIT" ]
trusted_remote_git push --atomic --no-follow-tags --signed=no --recurse-submodules=no \
  "$REMOTE_URL" "$COMMIT:refs/heads/main" \
  "refs/tags/$VERSION:refs/tags/$VERSION"
REMOTE_TAG_OBJECT="$(trusted_remote_git ls-remote --tags "$REMOTE_URL" \
  "refs/tags/$VERSION" | awk 'NR == 1 {print $1}')"
REMOTE_TAG_COMMIT="$(trusted_remote_git ls-remote --tags "$REMOTE_URL" \
  "refs/tags/$VERSION^{}" | awk 'NR == 1 {print $1}')"
[ "$REMOTE_TAG_OBJECT" = "$LOCAL_TAG_OBJECT" ]
[ "$REMOTE_TAG_COMMIT" = "$COMMIT" ]
TAG_RUN_ID="$(wait_for_matrix_run "$VERSION")"
```

这里的 main precheck 与 atomic push 能在 push 开始前远端 main 已推进时拒绝 tag，但不是跨两个 ref 的 compare-and-swap：若 Git 在 advertisement 后把无变化的 main ref 省略，另一发布者仍可能在该窗口推进 main 并留下 tag。因此上述跨主机独占 publication window 不能缩短为仅覆盖 tag push。

## Immutable draft 与发布

发布账号与 admin 权限已在任何 push 前确认。现在启用 immutable releases，再创建 draft；不得直接发布：

```bash
assert_publication_preconditions() {
  [ "$(gh api user --jq .login)" = longlannet ] || return 1
  [ "$(gh api "repos/$REPOSITORY" --jq '.permissions.admin')" = true ] || return 1
  assert_git_transport_config_safe || return 1
  [ -z "$(trusted_git status --porcelain=v1 --untracked-files=all)" ] || return 1
  [ "$(trusted_remote_git ls-remote --exit-code "$REMOTE_URL" \
    refs/heads/main | awk 'NR == 1 {print $1}')" = "$COMMIT" ] || return 1
  [ "$(trusted_remote_git ls-remote --tags "$REMOTE_URL" \
    "refs/tags/$VERSION" | awk 'NR == 1 {print $1}')" = "$LOCAL_TAG_OBJECT" ] || return 1
  [ "$(trusted_remote_git ls-remote --tags "$REMOTE_URL" \
    "refs/tags/$VERSION^{}" | awk 'NR == 1 {print $1}')" = "$COMMIT" ] || return 1
  assert_matrix_run_id "$MAIN_RUN_ID" || return 1
  assert_matrix_run_id "$TAG_RUN_ID"
}
assert_publication_preconditions
assert_release_name_unclaimed
gh api --method PUT "repos/$REPOSITORY/immutable-releases" >/dev/null
[ "$(gh api "repos/$REPOSITORY/immutable-releases" --jq .enabled)" = true ]

NOTES="$STAGE/RELEASE_NOTES.md"
# Write reviewed notes to $NOTES, including commit, signer fingerprint,
# publisher identity, test evidence, and the provenance limitation above.
assert_publication_preconditions
assert_release_name_unclaimed
gh release create "$VERSION" \
  "$STAGE/$ASSET" "$STAGE/SHA256SUMS" "$STAGE/SHA256SUMS.asc" \
  --repo "$REPOSITORY" --draft --verify-tag \
  --title "google-search ${VERSION}" --notes-file "$NOTES"
```

Draft 必须绑定 `longlannet` 作者、精确 tag、标题和逐字节一致的审阅正文，并恰好有 `google-search-${VERSION}.tar`、`SHA256SUMS`、`SHA256SUMS.asc` 三个资产。发布前下载回独立目录，完成 allowlist、逐字节、hash 和 GPG 验证，并再次确认 main/tag refs 与两次精确 commit CI 均未变化：

```bash
assert_release_identity() {
  local metadata
  metadata="$(gh release view "$VERSION" --repo "$REPOSITORY" \
    --json author,name,tagName --jq '[.tagName,.name,.author.login] | join("|")')"
  [ "$metadata" = "$VERSION|google-search $VERSION|longlannet" ]
  gh release view "$VERSION" --repo "$REPOSITORY" --json body --template '{{.body}}' \
    >"$VERIFY/RELEASE_NOTES.remote.md"
  cmp -- "$NOTES" "$VERIFY/RELEASE_NOTES.remote.md"
}
[ "$(gh release view "$VERSION" --repo "$REPOSITORY" --json isDraft --jq .isDraft)" = true ]
assert_release_identity
[ "$(gh release view "$VERSION" --repo "$REPOSITORY" --json assets \
  --jq '[.assets[].name] | sort | join("\n")')" = \
  "$(printf '%s\n' SHA256SUMS SHA256SUMS.asc "$ASSET" | sort)" ]
gh release download "$VERSION" --repo "$REPOSITORY" --dir "$VERIFY"
cmp -- "$STAGE/$ASSET" "$VERIFY/$ASSET"
cmp -- "$STAGE/SHA256SUMS" "$VERIFY/SHA256SUMS"
cmp -- "$STAGE/SHA256SUMS.asc" "$VERIFY/SHA256SUMS.asc"
(
  cd "$VERIFY"
  sha256sum --check --strict SHA256SUMS
)
verify_checksum_signature "$VERIFY"
assert_publication_preconditions
[ "$(gh api "repos/$REPOSITORY/immutable-releases" --jq .enabled)" = true ]
[ "$(gh release view "$VERSION" --repo "$REPOSITORY" --json isDraft --jq .isDraft)" = true ]
gh release edit "$VERSION" --repo "$REPOSITORY" \
  --draft=false --prerelease=false --latest --verify-tag
```

## 发布后终验

Immutable 只在 draft 发布后生效。终验必须同时覆盖 REST/CLI 状态、GitHub release attestation、每个资产以及无认证公网下载：

```bash
[ "$(gh release view "$VERSION" --repo "$REPOSITORY" \
  --json isDraft,isPrerelease,isImmutable \
  --jq '(.isDraft == false and .isPrerelease == false and .isImmutable == true)')" = true ]
[ "$(gh api "repos/$REPOSITORY/releases/latest" --jq .tag_name)" = "$VERSION" ]
assert_release_identity
assert_publication_preconditions
gh release verify "$VERSION" --repo "$REPOSITORY"
for file in "$VERIFY/$ASSET" "$VERIFY/SHA256SUMS" "$VERIFY/SHA256SUMS.asc"; do
  gh release verify-asset "$VERSION" "$file" --repo "$REPOSITORY"
done

PUBLIC="$(mktemp -d /tmp/google-search-release-public.XXXXXXXX)"
for name in "$ASSET" SHA256SUMS SHA256SUMS.asc; do
  env -i PATH=/usr/bin:/bin /usr/bin/curl -q --fail --location \
    --proto '=https' --tlsv1.2 --connect-timeout 10 --max-time 120 \
    --silent --show-error \
    --output "$PUBLIC/$name" \
    "https://github.com/$REPOSITORY/releases/download/$VERSION/$name"
  cmp -- "$VERIFY/$name" "$PUBLIC/$name"
done
(
  cd "$PUBLIC"
  sha256sum --check --strict SHA256SUMS
)
verify_checksum_signature "$PUBLIC"

stop_private_gpg_agent "$FRESH_KEYRING"
for directory in "$PUBLIC" "$VERIFY" "$STAGE" "$FRESH_KEYRING" "$AUDITED_DIRECTORY"; do
  remove_private_release_directory "$directory"
done
PUBLIC=
VERIFY=
STAGE=
FRESH_KEYRING=
AUDITED_DIRECTORY=
rmdir -- "$PUBLICATION_LOCK"
[ ! -e "$PUBLICATION_LOCK" ]
PUBLICATION_LOCK=
gh auth switch --hostname github.com --user "$ORIGINAL_GH_ACCOUNT"
[ "$(gh api user --jq .login)" = "$ORIGINAL_GH_ACCOUNT" ]
GH_ACCOUNT_SWITCHED=0
```

恢复原始 GitHub active account 后，另外删除并证明不存在本次 root-owned fresh checkout；上述命令已清理并证明 candidate、staging、download、public、fresh-keyring 和本机 publication lock 不存在。保留 commit、tag object、CI run IDs、Release ID、三个资产 SHA-256、签名 fingerprint 与终验结果作为发布证据。
