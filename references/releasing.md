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

正式 shell 的当前目录必须是以 `/tmp/google-search-release-checkout.XXXXXXXX` 命名、root-owned `0700` 父目录本身的 fresh standalone clone；不得从日常工作树、linked worktree 或共享 clone 发布。runbook 会登记这个 checkout，并在 evidence 完成前离开且删除它。在新 shell 中设置本次版本和受控环境，并拒绝不规范输入：

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
CURRENT_UID="$(id -u)" || exit 1
[[ "$CURRENT_UID" =~ ^(0|[1-9][0-9]*)$ ]]
command_output_is_exact() {
  local expected="$1"
  local observed
  shift
  observed="$("$@")" || return 1
  [ "$observed" = "$expected" ]
}
release_tmp_directory_is_safe() {
  local directory="$1"
  local canonical
  canonical="$(readlink -m -- "$directory")" || return 1
  [ "$canonical" = "$directory" ] || return 1
  [ -d "$directory" ] && [ ! -L "$directory" ] || return 1
  command_output_is_exact '0:1777' stat -c '%u:%a' -- "$directory"
}
release_tmp_directory_is_safe /tmp
BOOTSTRAP_RELEASE_CHECKOUT=
bootstrap_release_checkout_is_safe() {
  local directory="$1"
  local canonical
  [[ "$directory" =~ ^/tmp/google-search-release-checkout\.[A-Za-z0-9]{8}$ ]] || \
    return 1
  canonical="$(readlink -m -- "$directory")" || return 1
  [ "$canonical" = "$directory" ] || return 1
  [ -d "$directory" ] && [ ! -L "$directory" ] || return 1
  command_output_is_exact "$CURRENT_UID:700" stat -c '%u:%a' -- "$directory"
}
cleanup_bootstrap_release_checkout() {
  local status=$? cleanup_status=0
  trap - EXIT
  trap '' HUP INT TERM
  if [ -n "$BOOTSTRAP_RELEASE_CHECKOUT" ]; then
    if cd /root && \
      bootstrap_release_checkout_is_safe "$BOOTSTRAP_RELEASE_CHECKOUT" && \
      find -P "$BOOTSTRAP_RELEASE_CHECKOUT" -xdev -depth -delete && \
      [ ! -e "$BOOTSTRAP_RELEASE_CHECKOUT" ] && \
      [ ! -L "$BOOTSTRAP_RELEASE_CHECKOUT" ]; then
      BOOTSTRAP_RELEASE_CHECKOUT=
    else
      cleanup_status=1
    fi
  fi
  if [ "$status" -eq 0 ] && [ "$cleanup_status" -ne 0 ]; then
    status=1
  fi
  exit "$status"
}
BOOTSTRAP_RELEASE_CHECKOUT="$(pwd -P)" || exit 1
bootstrap_release_checkout_is_safe "$BOOTSTRAP_RELEASE_CHECKOUT" || exit 1
trap cleanup_bootstrap_release_checkout EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
REPOSITORY=longlannet/google-search
REMOTE_URL="https://github.com/$REPOSITORY.git"
VERSION=v2.0.0
SIGNING_FINGERPRINT=C678256ACBFC6491BF5076655F3AE24999921FFC
AUTHORITATIVE_KEY_URL=https://github.com/xxvcc.gpg
TEST_WORKFLOW_DATABASE_ID=244105293
[[ "$VERSION" =~ ^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]
[[ "$SIGNING_FINGERPRINT" =~ ^[0-9A-F]{40}$ ]]
[[ "$TEST_WORKFLOW_DATABASE_ID" =~ ^[1-9][0-9]*$ ]]
COMMIT=
PUBLICATION_LOCK_TARGET=/tmp/google-search.publication.lock
PUBLICATION_LOCK=
PUBLICATION_LOCK_IDENTITY=
GH_AUTH_LOCK_TARGET=/tmp/github-gh-auth.publication.lock
GH_AUTH_LOCK=
GH_AUTH_LOCK_IDENTITY=
CANARY=
STAGE=
VERIFY=
VERIFIED_ASSETS=
PUBLIC=
FRESH_KEYRING=
AUDITED_DIRECTORY=
RELEASE_CHECKOUT=
EVIDENCE_DIRECTORY=/root/google-search-release-evidence
EVIDENCE_FILE="$EVIDENCE_DIRECTORY/google-search-${VERSION}.txt"
EVIDENCE_TEMP=
EVIDENCE_COMPLETION_APPENDED=0
EVIDENCE_RECOVERY_READY=0
RELEASE_DATABASE_ID=
RELEASE_REST_ID=
RELEASE_ASSET_MANIFEST=
RELEASE_PUBLISHED_AT=
ANONYMOUS_LATEST_RELEASE_ID=
ARCHIVE_SHA256=
CHECKSUMS_SHA256=
CHECKSUM_SIGNATURE_SHA256=
ORIGINAL_GH_ACCOUNT=
GH_ACCOUNT_SWITCHED=0
MATRIX_RUN_DISCOVERY_TIMEOUT_SECONDS=300
MATRIX_RUN_POLL_SECONDS=5
MATRIX_RUN_WATCH_TIMEOUT_SECONDS=7200
POST_PUBLISH_RETRY_ATTEMPTS=12
POST_PUBLISH_RETRY_SECONDS=10
retry_post_publish_read_only() {
  local attempt=1
  [ "$POST_PUBLISH_RETRY_ATTEMPTS" -ge 1 ] || return 1
  [ "$POST_PUBLISH_RETRY_SECONDS" -ge 0 ] || return 1
  while ! "$@"; do
    [ "$attempt" -lt "$POST_PUBLISH_RETRY_ATTEMPTS" ] || return 1
    /usr/bin/sleep "$POST_PUBLISH_RETRY_SECONDS" || return 1
    attempt=$((attempt + 1)) || return 1
  done
}
set_release_signal_traps() {
  trap 'exit 129' HUP
  trap 'exit 130' INT
  trap 'exit 143' TERM
}
assert_git_local_config_safe() {
  local actual expected git_owner git_mode config_owner config_mode config_links
  local git_metadata config_metadata metadata_issue repository_root unsafe_path
  repository_root="$(pwd -P)" || return 1
  [ -d .git ] && [ ! -L .git ] || return 1
  command_output_is_exact "$repository_root/.git" \
    /usr/bin/readlink -m -- "$repository_root/.git" || return 1
  git_metadata="$(stat -c '%u:%a' -- .git)" || return 1
  IFS=: read -r git_owner git_mode <<<"$git_metadata" || return 1
  [ "$git_metadata" = "$git_owner:$git_mode" ] || return 1
  [ "$git_owner" = "$CURRENT_UID" ] || return 1
  case "$git_mode" in ''|*[!0-7]*) return 1 ;; esac
  [ $((8#$git_mode & 0022)) -eq 0 ] || return 1
  metadata_issue="$(
    /usr/bin/find -P .git -xdev \
      \( ! -uid "$CURRENT_UID" -o \
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
  config_metadata="$(stat -c '%u:%a:%h' -- .git/config)" || return 1
  IFS=: read -r config_owner config_mode config_links <<<"$config_metadata" || return 1
  [ "$config_metadata" = "$config_owner:$config_mode:$config_links" ] || return 1
  [ "$config_owner" = "$CURRENT_UID" ] && [ "$config_links" = 1 ] || return 1
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
    'branch.main.merge=refs/heads/main')" || return 1
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
  local http_config origin_url push_url rewrites status
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
  origin_url="$(trusted_git remote get-url origin)" || return 1
  [ "$origin_url" = "$REMOTE_URL" ] || return 1
  push_url="$(trusted_git remote get-url --push origin)" || return 1
  [ "$push_url" = "$REMOTE_URL" ] || return 1
}
remote_ref_oid() {
  local ref="$1"
  local output oid observed_ref extra
  case "$ref" in
    refs/heads/main|"refs/tags/$VERSION"|"refs/tags/$VERSION^{}") ;;
    *) return 1 ;;
  esac
  output="$(trusted_remote_git ls-remote --exit-code "$REMOTE_URL" "$ref")" || return 1
  IFS=$'\t' read -r oid observed_ref extra <<<"$output" || return 1
  [ -z "$extra" ] || return 1
  [ "$output" = "$oid"$'\t'"$observed_ref" ] || return 1
  [[ "$oid" =~ ^[0-9a-f]{40}$ ]] || return 1
  [ "$observed_ref" = "$ref" ] || return 1
  printf '%s\n' "$oid"
}
remote_release_tag_state_is_exact() {
  local output
  output="$(trusted_remote_git ls-remote --tags "$REMOTE_URL" \
    "refs/tags/$VERSION" "refs/tags/$VERSION^{}")" || return 1
  printf '%s\n' "$output" | awk \
    -v direct_ref="refs/tags/$VERSION" \
    -v peeled_ref="refs/tags/$VERSION^{}" \
    -v expected_direct="$LOCAL_TAG_OBJECT" \
    -v expected_peeled="$COMMIT" '
      NF != 2 {bad = 1; next}
      $2 == direct_ref {direct += 1; if ($1 != expected_direct) bad = 1; next}
      $2 == peeled_ref {peeled += 1; if ($1 != expected_peeled) bad = 1; next}
      {bad = 1}
      END {exit !(bad == 0 && direct == 1 && peeled == 1)}
    '
}
REPOSITORY_ROOT="$(pwd -P)" || exit 1
[[ "$REPOSITORY_ROOT" =~ ^/tmp/google-search-release-checkout\.[A-Za-z0-9]{8}$ ]]
[ -d "$REPOSITORY_ROOT" ] && [ ! -L "$REPOSITORY_ROOT" ]
command_output_is_exact "$REPOSITORY_ROOT" readlink -m -- "$REPOSITORY_ROOT"
command_output_is_exact "$CURRENT_UID:700" stat -c '%u:%a' -- "$REPOSITORY_ROOT"
RELEASE_CHECKOUT="$REPOSITORY_ROOT"
COMMIT="$(trusted_git rev-parse --verify 'HEAD^{commit}')" || exit 1
[[ "$COMMIT" =~ ^[0-9a-f]{40}$ ]]
command_output_is_exact "$REPOSITORY_ROOT" trusted_git rev-parse --show-toplevel
command_output_is_exact .git trusted_git rev-parse --git-dir
command_output_is_exact "$REPOSITORY_ROOT/.git" trusted_git rev-parse --absolute-git-dir
command_output_is_exact "$REPOSITORY_ROOT/.git" \
  trusted_git rev-parse --path-format=absolute --git-common-dir
command_output_is_exact main trusted_git symbolic-ref --quiet --short HEAD
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
  )" || return 1
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
  [[ "$directory" =~ ^/tmp/google-search-release(-(assets|verify|public|gnupg|canary|checkout))?\.[A-Za-z0-9]{8}$ ]]
}
private_gnupg_home_is_safe() {
  local directory="$1"
  local canonical
  [[ "$directory" =~ ^/tmp/google-search-release-gnupg\.[A-Za-z0-9]{8}$ ]] || return 1
  canonical="$(readlink -m -- "$directory")" || return 1
  [ "$canonical" = "$directory" ] || return 1
  [ -d "$directory" ] && [ ! -L "$directory" ] || return 1
  command_output_is_exact "$CURRENT_UID:700" stat -c '%u:%a' -- "$directory"
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
  [ "${BASH_REMATCH[1]}" = "$CURRENT_UID" ] || return 1
  if [ ! -e "$directory" ] && [ ! -L "$directory" ]; then
    return 0
  fi
  [ -d "$directory" ] && [ ! -L "$directory" ] || return 1
  command_output_is_exact "$CURRENT_UID:700" stat -c '%u:%a' -- "$directory"
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
  command_output_is_exact "$CURRENT_UID" stat -c %u -- "$directory" || return 1
  find -P "$directory" -xdev -depth -delete || return 1
  [ ! -e "$directory" ] && [ ! -L "$directory" ] || return 1
}
refresh_authoritative_keyring() {
  local key_file imported_primary_fingerprints
  [ "$AUTHORITATIVE_KEY_URL" = https://github.com/xxvcc.gpg ] || return 1
  if [ -n "${FRESH_KEYRING:-}" ]; then
    stop_private_gpg_agent "$FRESH_KEYRING" || return 1
    remove_private_release_directory "$FRESH_KEYRING" || return 1
    FRESH_KEYRING=
  fi
  FRESH_KEYRING="$(mktemp -d /tmp/google-search-release-gnupg.XXXXXXXX)" || return 1
  chmod 700 "$FRESH_KEYRING" || return 1
  private_gnupg_home_is_safe "$FRESH_KEYRING" || return 1
  key_file="$FRESH_KEYRING/xxvcc.gpg"
  env -i PATH=/usr/bin:/bin /usr/bin/curl -q --fail --location \
    --proto '=https' --proto-redir '=https' --tlsv1.2 \
    --connect-timeout 10 --max-time 120 \
    --silent --show-error --output "$key_file" "$AUTHORITATIVE_KEY_URL" || return 1
  [ -f "$key_file" ] && [ ! -L "$key_file" ] || return 1
  command_output_is_exact "$CURRENT_UID:600:1" \
    stat -c '%u:%a:%h' -- "$key_file" || return 1
  GNUPGHOME="$FRESH_KEYRING" /usr/bin/gpg --batch --import "$key_file" || return 1
  private_gnupg_home_is_safe "$FRESH_KEYRING" || return 1
  imported_primary_fingerprints="$(
    GNUPGHOME="$FRESH_KEYRING" /usr/bin/gpg --batch \
      --with-colons --list-keys --fingerprint |
      awk -F: '$1 == "pub" {want = 1; next} want && $1 == "fpr" {print $10; want = 0}'
  )" || return 1
  [ "$imported_primary_fingerprints" = "$SIGNING_FINGERPRINT" ] || return 1
}
prepare_release_evidence_target() {
  local parent_mode
  [ "$EVIDENCE_DIRECTORY" = /root/google-search-release-evidence ] || return 1
  [ "$EVIDENCE_FILE" = "$EVIDENCE_DIRECTORY/google-search-${VERSION}.txt" ] || return 1
  [ -d /root ] && [ ! -L /root ] || return 1
  command_output_is_exact /root readlink -m -- /root || return 1
  command_output_is_exact "$CURRENT_UID" stat -c %u -- /root || return 1
  parent_mode="$(stat -c %a -- /root)" || return 1
  case "$parent_mode" in ''|*[!0-7]*) return 1 ;; esac
  [ $((8#$parent_mode & 0022)) -eq 0 ] || return 1
  if [ ! -e "$EVIDENCE_DIRECTORY" ] && [ ! -L "$EVIDENCE_DIRECTORY" ]; then
    mkdir -m 700 -- "$EVIDENCE_DIRECTORY" || return 1
  fi
  command_output_is_exact "$EVIDENCE_DIRECTORY" \
    readlink -m -- "$EVIDENCE_DIRECTORY" || return 1
  [ -d "$EVIDENCE_DIRECTORY" ] && [ ! -L "$EVIDENCE_DIRECTORY" ] || return 1
  command_output_is_exact "$CURRENT_UID:700" \
    stat -c '%u:%a' -- "$EVIDENCE_DIRECTORY" || return 1
  [ ! -e "$EVIDENCE_FILE" ] && [ ! -L "$EVIDENCE_FILE" ]
}
release_evidence_temp_is_safe() {
  local expected_prefix
  expected_prefix="$EVIDENCE_DIRECTORY/.google-search-${VERSION}.evidence."
  case "$EVIDENCE_TEMP" in "$expected_prefix"????????) ;; *) return 1 ;; esac
  command_output_is_exact "$EVIDENCE_TEMP" readlink -m -- "$EVIDENCE_TEMP" || return 1
  [ -f "$EVIDENCE_TEMP" ] && [ ! -L "$EVIDENCE_TEMP" ] || return 1
  command_output_is_exact "$CURRENT_UID:600:1" \
    stat -c '%u:%a:%h' -- "$EVIDENCE_TEMP"
}
remove_release_evidence_temp() {
  release_evidence_temp_is_safe || return 1
  rm -- "$EVIDENCE_TEMP" || return 1
  [ ! -e "$EVIDENCE_TEMP" ] && [ ! -L "$EVIDENCE_TEMP" ] || return 1
  EVIDENCE_TEMP=
}
cleanup_release_state() {
  status=$?
  local can_release_locks=1 restored_account=''
  trap - EXIT
  trap '' HUP INT TERM
  cleanup_status=0
  if [ -n "${RELEASE_CHECKOUT:-}" ]; then
    cd /root || cleanup_status=1
  fi
  if [ -n "${FRESH_KEYRING:-}" ]; then
    if stop_private_gpg_agent "$FRESH_KEYRING"; then
      remove_private_release_directory "$FRESH_KEYRING" || cleanup_status=1
    else
      cleanup_status=1
    fi
  fi
  for directory in "${PUBLIC:-}" "${VERIFY:-}" "${STAGE:-}" \
    "${CANARY:-}" "${AUDITED_DIRECTORY:-}" "${RELEASE_CHECKOUT:-}"; do
    [ -z "$directory" ] || remove_private_release_directory "$directory" || cleanup_status=1
  done
  case "${EVIDENCE_RECOVERY_READY:-0}" in
    0)
      [ -z "${EVIDENCE_TEMP:-}" ] || \
        remove_release_evidence_temp || cleanup_status=1
      ;;
    1)
      if [ -n "${EVIDENCE_TEMP:-}" ] && release_evidence_temp_is_safe; then
        printf 'recoverable release evidence preserved: %s\n' "$EVIDENCE_TEMP" >&2
      else
        printf '%s\n' 'release evidence recovery state is unsafe; refusing deletion' >&2
      fi
      cleanup_status=1
      ;;
    *)
      printf '%s\n' 'invalid release evidence recovery state; refusing deletion' >&2
      cleanup_status=1
      ;;
  esac
  if [ "${GH_ACCOUNT_SWITCHED:-0}" -eq 1 ]; then
    if [ -n "${ORIGINAL_GH_ACCOUNT:-}" ] && \
      timeout --signal=TERM --kill-after=5s 30s \
        gh auth switch --hostname github.com --user "$ORIGINAL_GH_ACCOUNT" >/dev/null &&
      restored_account="$(timeout --signal=TERM --kill-after=5s 30s \
        gh api user --jq .login)" && \
      [ "$restored_account" = "$ORIGINAL_GH_ACCOUNT" ]; then
      GH_ACCOUNT_SWITCHED=0
    else
      cleanup_status=1
      can_release_locks=0
    fi
  fi
  if [ "$can_release_locks" -eq 1 ] && [ -n "${GH_AUTH_LOCK:-}" ]; then
    if [ "$GH_AUTH_LOCK" = "$GH_AUTH_LOCK_TARGET" ] && \
      [[ "$GH_AUTH_LOCK_IDENTITY" =~ ^[0-9]+:[0-9]+$ ]] && \
      command_output_is_exact "$GH_AUTH_LOCK_IDENTITY" \
        stat -c '%d:%i' -- "$GH_AUTH_LOCK"; then
      if ! rmdir -- "$GH_AUTH_LOCK" >/dev/null 2>&1; then
        cleanup_status=1
        can_release_locks=0
      else
        GH_AUTH_LOCK=
        GH_AUTH_LOCK_IDENTITY=
      fi
    else
      cleanup_status=1
      can_release_locks=0
    fi
  fi
  if [ "$can_release_locks" -eq 1 ] && [ -n "${PUBLICATION_LOCK:-}" ]; then
    if [ "$PUBLICATION_LOCK" = "$PUBLICATION_LOCK_TARGET" ] && \
      [[ "$PUBLICATION_LOCK_IDENTITY" =~ ^[0-9]+:[0-9]+$ ]] && \
      command_output_is_exact "$PUBLICATION_LOCK_IDENTITY" \
        stat -c '%d:%i' -- "$PUBLICATION_LOCK"; then
      if rmdir -- "$PUBLICATION_LOCK" >/dev/null 2>&1; then
        PUBLICATION_LOCK=
        PUBLICATION_LOCK_IDENTITY=
      else
        cleanup_status=1
      fi
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
BOOTSTRAP_RELEASE_CHECKOUT=
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
command_output_is_exact "$CURRENT_UID:700" stat -c '%u:%a' -- "$PUBLICATION_LOCK"
PUBLICATION_LOCK_IDENTITY="$(stat -c '%d:%i' -- "$PUBLICATION_LOCK")" || exit 1
[[ "$PUBLICATION_LOCK_IDENTITY" =~ ^[0-9]+:[0-9]+$ ]]
if mkdir -m 700 -- "$GH_AUTH_LOCK_TARGET"; then
  GH_AUTH_LOCK="$GH_AUTH_LOCK_TARGET"
else
  lock_status=$?
  trap 'exit 129' HUP
  trap 'exit 130' INT
  trap 'exit 143' TERM
  exit "$lock_status"
fi
set_release_signal_traps
command_output_is_exact "$CURRENT_UID:700" stat -c '%u:%a' -- "$GH_AUTH_LOCK"
GH_AUTH_LOCK_IDENTITY="$(stat -c '%d:%i' -- "$GH_AUTH_LOCK")" || exit 1
[[ "$GH_AUTH_LOCK_IDENTITY" =~ ^[0-9]+:[0-9]+$ ]]
prepare_release_evidence_target
```

仓库级 lock 排除本机对这个仓库的所有版本并行发布，主机级 `gh` lock 排除其他仓库同时切换全局 active account；两者都不是跨主机协调器。必须按仓库 lock、`gh` lock 的固定顺序获取，在恢复原账号后按相反顺序释放。残留 lock 必须人工确认无活动发布后处理，不能自动抢占。Git guard 会在每次调用前遍历 `.git`：只接受当前 uid 所有、不可由 group/other 写入的普通目录和单链接普通文件，并拒绝 `commondir`、worktree config、legacy `info/grafts`、object alternates、symlink 与特殊文件；因此共享 clone、linked worktree 或重定向 Git 元数据的 checkout 不能用于正式发布。

## Signed commit 与 main CI

紧邻签名 commit 前从固定的签名者权威 HTTPS 渠道重新下载 public key，导入本次新建的 private keyring，并确认其中只有预期 primary fingerprint；不得使用历史缓存、旧导出或仅从本机默认 keyring 再导出的副本。然后只 stage 已逐项审阅的文件，确认 staged manifest 后显式签名；不要使用会把未知文件一并纳入的发布别名或脚本：

```bash
refresh_authoritative_keyring
trusted_git diff --cached --check --no-ext-diff --no-textconv
trusted_git diff --cached --name-status --no-ext-diff --no-textconv
command_output_is_exact XXV.CC trusted_git config --get user.name
command_output_is_exact github@xxv.cc trusted_git config --get user.email
CREDENTIAL_HELPERS="$(trusted_remote_git config \
  --get-all credential.https://github.com.helper | awk 'NF')" || exit 1
[ "$CREDENTIAL_HELPERS" = '!/usr/bin/gh auth git-credential' ]
SECRET_PRIMARY_FINGERPRINT="$(/usr/bin/gpg --batch --with-colons --list-secret-keys \
  "$SIGNING_FINGERPRINT" | awk -F: '$1 == "fpr" {print $10; exit}')" || exit 1
[ "$SECRET_PRIMARY_FINGERPRINT" = "$SIGNING_FINGERPRINT" ]
ORIGINAL_GH_ACCOUNT="$(timeout --signal=TERM --kill-after=5s 30s \
  gh api user --jq .login)" || exit 1
[ "$ORIGINAL_GH_ACCOUNT" = xxvcc ]
trusted_git commit -S"$SIGNING_FINGERPRINT" -m "release: google-search ${VERSION}"
COMMIT="$(trusted_git rev-parse --verify 'HEAD^{commit}')" || exit 1
[[ "$COMMIT" =~ ^[0-9a-f]{40}$ ]]
verify_git_commit_signature "$COMMIT"
```

上述 helper 只接受恰好一个 `VALIDSIG`，要求其 primary fingerprint 精确等于完整预期值且摘要算法为 SHA-256，并拒绝 bad/error、过期或撤销的签名与 key 状态。在任何远端写入前，先在 root-owned 私有 fresh checkout 中执行 README 的完整候选配方，把成功时打印的绝对路径赋给 `AUDITED_ARCHIVE`。然后从这个精确归档执行一次全新依赖安装、真实 Serper smoke 和 full selfcheck；canary 只在私有目录中短暂复制受保护 key，quiet 模式不保留结果文件，退出 trap 会在失败时同样清理 key、venv 与目录。真实检查可能计费。

```bash
AUDITED_ARCHIVE="${AUDITED_ARCHIVE:?set the path printed by the audited recipe}"
AUDITED_DIRECTORY_CANDIDATE="${AUDITED_ARCHIVE%/*}"
private_release_directory_path_is_valid "$AUDITED_DIRECTORY_CANDIDATE"
command_output_is_exact "$AUDITED_DIRECTORY_CANDIDATE" \
  readlink -m -- "$AUDITED_DIRECTORY_CANDIDATE"
[ -d "$AUDITED_DIRECTORY_CANDIDATE" ] && [ ! -L "$AUDITED_DIRECTORY_CANDIDATE" ]
command_output_is_exact "$CURRENT_UID:700" \
  stat -c '%u:%a' -- "$AUDITED_DIRECTORY_CANDIDATE"
AUDITED_DIRECTORY="$AUDITED_DIRECTORY_CANDIDATE"
[[ "$AUDITED_ARCHIVE" =~ ^/tmp/google-search-release\.[A-Za-z0-9]{8}/google-search-$COMMIT\.tar$ ]]
command_output_is_exact "$AUDITED_ARCHIVE" readlink -m -- "$AUDITED_ARCHIVE"
[ "${AUDITED_ARCHIVE##*/}" = "google-search-${COMMIT}.tar" ]
[ -f "$AUDITED_ARCHIVE" ] && [ ! -L "$AUDITED_ARCHIVE" ]
command_output_is_exact "$CURRENT_UID:600:1" \
  stat -c '%u:%a:%h' -- "$AUDITED_ARCHIVE"

SERPER_CONFIG_SOURCE="${SERPER_CONFIG_SOURCE:?set an absolute protected Serper config path}"
case "$SERPER_CONFIG_SOURCE" in /*) ;; *) false ;; esac
[ -f "$SERPER_CONFIG_SOURCE" ] && [ ! -L "$SERPER_CONFIG_SOURCE" ]
command_output_is_exact "$CURRENT_UID:600:1" \
  stat -c '%u:%a:%h' -- "$SERPER_CONFIG_SOURCE"
SERPER_CONFIG_METADATA="$(stat -c '%d:%i:%u:%g:%a:%h:%s:%y:%z' \
  -- "$SERPER_CONFIG_SOURCE")" || exit 1
CANARY="$(mktemp -d /tmp/google-search-release-canary.XXXXXXXX)" || exit 1
command_output_is_exact "$CURRENT_UID:700" stat -c '%u:%a' -- "$CANARY"
tar --extract --file "$AUDITED_ARCHIVE" --directory "$CANARY"
command_output_is_exact "$SERPER_CONFIG_METADATA" \
  stat -c '%d:%i:%u:%g:%a:%h:%s:%y:%z' -- "$SERPER_CONFIG_SOURCE"
install -m 600 -- "$SERPER_CONFIG_SOURCE" "$CANARY/config/serper.env"
cmp -- "$SERPER_CONFIG_SOURCE" "$CANARY/config/serper.env"
command_output_is_exact "$SERPER_CONFIG_METADATA" \
  stat -c '%d:%i:%u:%g:%a:%h:%s:%y:%z' -- "$SERPER_CONFIG_SOURCE"
command_output_is_exact "$CURRENT_UID:600:1" \
  stat -c '%u:%a:%h' -- "$CANARY/config/serper.env"
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
command_output_is_exact longlannet gh api user --jq .login
command_output_is_exact true gh api "repos/$REPOSITORY" --jq '.permissions.admin'
command_output_is_exact '' trusted_git status --porcelain=v1 --untracked-files=all
assert_git_transport_config_safe
assert_tag_name_unclaimed
assert_release_name_unclaimed
trusted_remote_git fetch --no-tags --no-prune --recurse-submodules=no \
  --no-write-fetch-head --no-write-commit-graph --no-auto-maintenance "$REMOTE_URL" \
  refs/heads/main:refs/remotes/origin/main
REMOTE_MAIN="$(remote_ref_oid refs/heads/main)" || exit 1
[[ "$REMOTE_MAIN" =~ ^[0-9a-f]{40}$ ]]
command_output_is_exact "$REMOTE_MAIN" \
  trusted_git rev-parse --verify refs/remotes/origin/main
trusted_git merge-base --is-ancestor "$REMOTE_MAIN" "$COMMIT"

push_main_with_reconciliation() {
  local push_status=0 observed_main
  if trusted_remote_git push --atomic --no-follow-tags --signed=no \
    --recurse-submodules=no "$REMOTE_URL" "$COMMIT:refs/heads/main"; then
    :
  else
    push_status=$?
  fi
  reconcile_main_push_once() {
    local observed_main
    observed_main="$(remote_ref_oid refs/heads/main)" || return 1
    [ "$observed_main" = "$COMMIT" ]
  }
  if retry_post_publish_read_only reconcile_main_push_once; then
    if [ "$push_status" -ne 0 ]; then
      printf '%s\n' 'main push reported failure; exact remote commit was recovered' >&2
    fi
    return 0
  fi
  observed_main="$(remote_ref_oid refs/heads/main)" || return 1
  if [ "$push_status" -ne 0 ] && [ "$observed_main" = "$REMOTE_MAIN" ]; then
    printf '%s\n' 'main push made no remote change; stop before retrying' >&2
    return "$push_status"
  fi
  printf '%s\n' 'main push outcome is absent, unchanged, or conflicting; stop' >&2
  return 1
}
push_main_with_reconciliation

assert_matrix_run_id() {
  local run_id="$1"
  local expected_branch="$2"
  local metadata run_database_id run_sha run_branch run_event run_status run_conclusion
  local workflow_id workflow_name expected_jobs actual_jobs
  [[ "$run_id" =~ ^[1-9][0-9]*$ ]] || return 1
  case "$expected_branch" in main|"$VERSION") ;; *) return 1 ;; esac
  metadata="$(gh run view "$run_id" --repo "$REPOSITORY" \
    --json databaseId,headSha,headBranch,event,status,conclusion,workflowDatabaseId,workflowName \
    --jq '[.databaseId,.headSha,.headBranch,.event,.status,.conclusion,.workflowDatabaseId,.workflowName] | map(tostring) | join("|")')" || return 1
  IFS='|' read -r run_database_id run_sha run_branch run_event run_status \
    run_conclusion workflow_id workflow_name <<<"$metadata" || return 1
  [ "$metadata" = "$run_database_id|$run_sha|$run_branch|$run_event|$run_status|$run_conclusion|$workflow_id|$workflow_name" ] || return 1
  [ "$run_database_id" = "$run_id" ] || return 1
  [ "$run_sha" = "$COMMIT" ] || return 1
  [ "$run_branch" = "$expected_branch" ] || return 1
  [ "$run_event" = push ] || return 1
  [ "$run_status" = completed ] || return 1
  [ "$run_conclusion" = success ] || return 1
  [ "$workflow_id" = "$TEST_WORKFLOW_DATABASE_ID" ] || return 1
  [ "$workflow_name" = test ] || return 1
  expected_jobs="$(printf 'Python %s|completed|success\n' \
    3.10 3.11 3.12 3.13 3.14 | sort)" || return 1
  actual_jobs="$(
    gh run view "$run_id" --repo "$REPOSITORY" --json jobs \
      --jq '[.jobs[] | [.name,.status,.conclusion] | join("|")] | sort | join("\n")'
  )" || return 1
  [ "$actual_jobs" = "$expected_jobs" ] || return 1
}

watch_matrix_run() {
  local run_id="$1"
  [[ "$run_id" =~ ^[1-9][0-9]*$ ]] || return 1
  /usr/bin/timeout --signal=TERM --kill-after=10s \
    "${MATRIX_RUN_WATCH_TIMEOUT_SECONDS}s" /usr/bin/gh run watch "$run_id" \
      --repo "$REPOSITORY" --exit-status >/dev/null || return 1
}

wait_for_matrix_run() {
  local branch="$1"
  local run_id='' deadline
  MATRIX_RUN_ID=
  case "$branch" in main|"$VERSION") ;; *) return 1 ;; esac
  deadline=$((SECONDS + MATRIX_RUN_DISCOVERY_TIMEOUT_SECONDS)) || return 1
  while [ -z "$run_id" ]; do
    run_id="$(
      gh run list --repo "$REPOSITORY" --workflow test.yml --branch "$branch" \
        --commit "$COMMIT" --event push --limit 20 \
        --json databaseId --jq '.[0].databaseId // empty'
    )" || return 1
    case "$run_id" in '' ) ;; 0|*[!0-9]*) return 1 ;; esac
    [ -n "$run_id" ] && break
    [ "$SECONDS" -lt "$deadline" ] || return 1
    /usr/bin/sleep "$MATRIX_RUN_POLL_SECONDS" || return 1
  done
  [ -n "$run_id" ] || return 1
  watch_matrix_run "$run_id" || return 1
  assert_matrix_run_id "$run_id" "$branch" || return 1
  MATRIX_RUN_ID="$run_id"
}

wait_for_matrix_run main
MAIN_RUN_ID="$MATRIX_RUN_ID"
```

记录 `MAIN_RUN_ID`；后续不能用旧 run 或其他 commit 的绿色状态代替。

## 候选资产与签名 tag

复用任何远端写入前已经通过完整门禁和真实 canary 的 `AUDITED_ARCHIVE`；版本化副本必须逐字节相同：

```bash
[ -f "$AUDITED_ARCHIVE" ] && [ ! -L "$AUDITED_ARCHIVE" ]
STAGE="$(mktemp -d /tmp/google-search-release-assets.XXXXXXXX)" || exit 1
VERIFY="$(mktemp -d /tmp/google-search-release-verify.XXXXXXXX)" || exit 1
command_output_is_exact "$CURRENT_UID:700" stat -c '%u:%a' -- "$STAGE"
command_output_is_exact "$CURRENT_UID:700" stat -c '%u:%a' -- "$VERIFY"
ASSET="google-search-${VERSION}.tar"
EXPECTED_ALL_ASSET_NAMES="$(
  printf '%s\n' SHA256SUMS SHA256SUMS.asc "$ASSET" | sort
)" || exit 1
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

不得继续复用 commit 前的 key snapshot。紧邻 tag 创建前再次从固定权威渠道下载 key，替换为第二个 fresh `GNUPGHOME`，重新验证 signed commit、`SHA256SUMS.asc` 和 tar hash。历史缓存或旧导出即使 fingerprint 相同也不能证明当前未撤销；仅从同一主机导出再导入 key 只能检查文件自洽，不能建立签名者身份：

```bash
refresh_authoritative_keyring
verify_git_commit_signature "$COMMIT"
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
command_output_is_exact "$COMMIT" trusted_git rev-parse --verify "$VERSION^{commit}"
LOCAL_TAG_OBJECT="$(trusted_git rev-parse --verify "refs/tags/$VERSION")" || exit 1
[[ "$LOCAL_TAG_OBJECT" =~ ^[0-9a-f]{40}$ ]]
REMOTE_MAIN_BEFORE_TAG="$(remote_ref_oid refs/heads/main)" || exit 1
[ "$REMOTE_MAIN_BEFORE_TAG" = "$COMMIT" ]

push_tag_with_reconciliation() {
  local push_status=0
  if trusted_remote_git push --atomic --no-follow-tags --signed=no \
    --recurse-submodules=no "$REMOTE_URL" "$COMMIT:refs/heads/main" \
    "refs/tags/$VERSION:refs/tags/$VERSION"; then
    :
  else
    push_status=$?
  fi
  reconcile_tag_push_once() {
    local observed_main
    observed_main="$(remote_ref_oid refs/heads/main)" || return 1
    [ "$observed_main" = "$COMMIT" ] || return 1
    remote_release_tag_state_is_exact || return 1
  }
  if retry_post_publish_read_only reconcile_tag_push_once; then
    REMOTE_TAG_OBJECT="$LOCAL_TAG_OBJECT"
    REMOTE_TAG_COMMIT="$COMMIT"
    if [ "$push_status" -ne 0 ]; then
      printf '%s\n' 'tag push reported failure; exact remote refs were recovered' >&2
    fi
    return 0
  fi
  printf '%s\n' 'tag push remote state is absent, partial, or conflicting; stop' >&2
  return 1
}
push_tag_with_reconciliation
wait_for_matrix_run "$VERSION"
TAG_RUN_ID="$MATRIX_RUN_ID"
[ "$TAG_RUN_ID" != "$MAIN_RUN_ID" ]
```

这里的 main precheck 与 atomic push 能在 push 开始前远端 main 已推进时拒绝 tag，但不是跨两个 ref 的 compare-and-swap：若 Git 在 advertisement 后把无变化的 main ref 省略，另一发布者仍可能在该窗口推进 main 并留下 tag。因此上述跨主机独占 publication window 不能缩短为仅覆盖 tag push。

## Immutable draft 与发布

发布账号与 admin 权限已在任何 push 前确认。Tag CI 全绿后必须第三次刷新权威 key snapshot，重新验证 commit、tag 与 checksum；这一步必须紧邻 immutable/draft，不能复用 tag 前的 snapshot。随后启用 immutable releases，先创建不带资产的 draft，再逐个无覆盖上传资产；不得直接发布，也不得使用 `--clobber`：

```bash
assert_publication_preconditions() {
  local observed_main
  command_output_is_exact longlannet gh api user --jq .login || return 1
  command_output_is_exact true \
    gh api "repos/$REPOSITORY" --jq '.permissions.admin' || return 1
  assert_git_transport_config_safe || return 1
  command_output_is_exact '' \
    trusted_git status --porcelain=v1 --untracked-files=all || return 1
  observed_main="$(remote_ref_oid refs/heads/main)" || return 1
  [ "$observed_main" = "$COMMIT" ] || return 1
  remote_release_tag_state_is_exact || return 1
  [ "$MAIN_RUN_ID" != "$TAG_RUN_ID" ] || return 1
  assert_matrix_run_id "$MAIN_RUN_ID" main || return 1
  assert_matrix_run_id "$TAG_RUN_ID" "$VERSION" || return 1
}
assert_release_identity() {
  local metadata
  metadata="$(gh release view "$VERSION" --repo "$REPOSITORY" \
    --json author,name,tagName --jq '[.tagName,.name,.author.login] | join("|")')" || return 1
  [ "$metadata" = "$VERSION|google-search $VERSION|longlannet" ] || return 1
  gh release view "$VERSION" --repo "$REPOSITORY" --json body --template '{{.body}}' \
    >"$VERIFY/RELEASE_NOTES.remote.md" || return 1
  [ -f "$VERIFY/RELEASE_NOTES.remote.md" ] && \
    [ ! -L "$VERIFY/RELEASE_NOTES.remote.md" ] || return 1
  command_output_is_exact "$CURRENT_UID:600:1" \
    stat -c '%u:%a:%h' -- "$VERIFY/RELEASE_NOTES.remote.md" || return 1
  cmp -- "$NOTES" "$VERIFY/RELEASE_NOTES.remote.md" || return 1
}
capture_release_ids() {
  local database_id rest_id
  [ -z "$RELEASE_DATABASE_ID" ] && [ -z "$RELEASE_REST_ID" ] || return 1
  database_id="$(gh release view "$VERSION" --repo "$REPOSITORY" \
    --json databaseId --jq .databaseId)" || return 1
  rest_id="$(gh api "repos/$REPOSITORY/releases/tags/$VERSION" --jq .id)" || return 1
  [[ "$database_id" =~ ^[1-9][0-9]*$ ]] || return 1
  [ "$rest_id" = "$database_id" ] || return 1
  RELEASE_DATABASE_ID="$database_id"
  RELEASE_REST_ID="$rest_id"
}
assert_release_ids_exact() {
  local database_id rest_id
  [[ "$RELEASE_DATABASE_ID" =~ ^[1-9][0-9]*$ ]] || return 1
  [ "$RELEASE_REST_ID" = "$RELEASE_DATABASE_ID" ] || return 1
  database_id="$(gh release view "$VERSION" --repo "$REPOSITORY" \
    --json databaseId --jq .databaseId)" || return 1
  rest_id="$(gh api "repos/$REPOSITORY/releases/tags/$VERSION" --jq .id)" || return 1
  [ "$database_id" = "$RELEASE_DATABASE_ID" ] || return 1
  [ "$rest_id" = "$RELEASE_REST_ID" ] || return 1
}
sha256_of_file() {
  local file="$1"
  local line digest
  line="$(sha256sum -- "$file")" || return 1
  digest="${line%% *}"
  [[ "$digest" =~ ^[0-9a-f]{64}$ ]] || return 1
  printf '%s\n' "$digest"
}
observe_release_asset_manifest() {
  local expected_names="$1"
  local source_directory="$2"
  local raw_manifest manifest observed_names='' seen_ids=':'
  local name asset_id size digest extra local_size local_digest
  [[ "$RELEASE_REST_ID" =~ ^[1-9][0-9]*$ ]] || return 1
  case "$source_directory" in
    "$STAGE") ;;
    "$VERIFIED_ASSETS") [ -n "$VERIFIED_ASSETS" ] || return 1 ;;
    *) return 1 ;;
  esac
  raw_manifest="$(gh api "repos/$REPOSITORY/releases/tags/$VERSION" --jq '
    if (
      ((.id | type) == "number") and (.id > 0) and
      ((.assets | type) == "array") and
      all(.assets[];
        ((.id | type) == "number") and (.id > 0) and
        ((.name | type) == "string") and
        ((.size | type) == "number") and (.size > 0) and
        (.state == "uploaded") and
        ((.digest | type) == "string"))
    ) then
      (([["release", (.id | tostring), "", ""]] +
        ([.assets[] | [.name, (.id | tostring), (.size | tostring), .digest]] |
          sort_by(.[0])))[] | join("|"))
    else error("invalid release asset manifest") end
  ')" || return 1
  case "$raw_manifest" in
    "release|$RELEASE_REST_ID||") manifest= ;;
    "release|$RELEASE_REST_ID||"$'\n'*)
      manifest="${raw_manifest#*$'\n'}"
      ;;
    *) return 1 ;;
  esac
  if [ -n "$manifest" ]; then
    while IFS='|' read -r name asset_id size digest extra; do
      [ -z "$extra" ] || return 1
      case "$name" in "$ASSET"|SHA256SUMS|SHA256SUMS.asc) ;; *) return 1 ;; esac
      [[ "$asset_id" =~ ^[1-9][0-9]*$ ]] || return 1
      [[ "$size" =~ ^[1-9][0-9]*$ ]] || return 1
      [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || return 1
      case "$seen_ids" in *":$asset_id:"*) return 1 ;; esac
      seen_ids="$seen_ids$asset_id:"
      if [ -z "$observed_names" ]; then
        observed_names="$name"
      else
        observed_names="$observed_names
$name"
      fi
      [ -f "$source_directory/$name" ] && [ ! -L "$source_directory/$name" ] || return 1
      command_output_is_exact "$CURRENT_UID:600:1" \
        stat -c '%u:%a:%h' -- "$source_directory/$name" || return 1
      local_size="$(stat -c %s -- "$source_directory/$name")" || return 1
      [ "$local_size" = "$size" ] || return 1
      local_digest="$(sha256_of_file "$source_directory/$name")" || return 1
      [ "sha256:$local_digest" = "$digest" ] || return 1
    done <<<"$manifest"
  fi
  [ "$observed_names" = "$expected_names" ] || return 1
  printf '%s\n' "$manifest"
}
assert_release_asset_names() {
  local expected_names="$1"
  observe_release_asset_manifest "$expected_names" "$STAGE" >/dev/null
}
assert_release_asset_manifest_exact() {
  local source_directory="$1"
  local observed
  [ -n "$RELEASE_ASSET_MANIFEST" ] || return 1
  observed="$(observe_release_asset_manifest \
    "$EXPECTED_ALL_ASSET_NAMES" "$source_directory")" || return 1
  [ "$observed" = "$RELEASE_ASSET_MANIFEST" ] || return 1
}
capture_release_asset_manifest() {
  local observed
  [ -z "$RELEASE_ASSET_MANIFEST" ] || return 1
  observed="$(observe_release_asset_manifest \
    "$EXPECTED_ALL_ASSET_NAMES" "$STAGE")" || return 1
  [ -n "$observed" ] || return 1
  RELEASE_ASSET_MANIFEST="$observed"
}
assert_exact_draft() {
  local state
  state="$(gh release view "$VERSION" --repo "$REPOSITORY" \
    --json isDraft,isPrerelease,isImmutable \
    --jq '[.isDraft,.isPrerelease,.isImmutable] | join("|")')" || return 1
  [ "$state" = 'true|false|false' ] || return 1
  assert_release_identity || return 1
}
verify_uploaded_release_asset() {
  local source="$1"
  local expected_names="$2"
  local name destination finding manifest asset_id manifest_after
  name="${source##*/}"
  case "$name" in "$ASSET"|SHA256SUMS|SHA256SUMS.asc) ;; *) return 1 ;; esac
  assert_exact_draft || return 1
  assert_release_ids_exact || return 1
  manifest="$(observe_release_asset_manifest "$expected_names" "$STAGE")" || return 1
  asset_id="$(printf '%s\n' "$manifest" | awk -F'|' \
    -v expected="$name" '$1 == expected {found += 1; value = $2} END {if (found == 1) print value; else exit 1}')" || return 1
  [[ "$asset_id" =~ ^[1-9][0-9]*$ ]] || return 1
  destination="$(mktemp -d "$VERIFY/upload-$name.XXXXXXXX")" || return 1
  command_output_is_exact "$CURRENT_UID:700" stat -c '%u:%a' -- "$destination" || return 1
  gh api --header 'Accept: application/octet-stream' \
    "repos/$REPOSITORY/releases/assets/$asset_id" >"$destination/$name" || return 1
  [ -f "$destination/$name" ] && [ ! -L "$destination/$name" ] || return 1
  command_output_is_exact "$CURRENT_UID:600:1" \
    stat -c '%u:%a:%h' -- "$destination/$name" || return 1
  finding="$(find -P "$destination" -mindepth 1 ! -path "$destination/$name" \
    -print -quit)" || return 1
  [ -z "$finding" ] || return 1
  cmp -- "$source" "$destination/$name" || return 1
  manifest_after="$(observe_release_asset_manifest "$expected_names" "$STAGE")" || return 1
  [ "$manifest_after" = "$manifest" ] || return 1
}
create_draft_with_reconciliation() {
  local create_status=0
  if gh release create "$VERSION" --repo "$REPOSITORY" --draft --verify-tag \
    --title "google-search ${VERSION}" --notes-file "$NOTES"; then
    :
  else
    create_status=$?
  fi
  retry_post_publish_read_only assert_exact_draft || return 1
  retry_post_publish_read_only capture_release_ids || return 1
  retry_post_publish_read_only assert_release_ids_exact || return 1
  retry_post_publish_read_only assert_release_asset_names '' || return 1
  if [ "$create_status" -ne 0 ]; then
    printf '%s\n' 'draft create reported failure; exact empty draft was recovered' >&2
  fi
}
upload_asset_with_reconciliation() {
  local source="$1"
  local expected_names="$2"
  local upload_status=0
  if gh release upload "$VERSION" "$source" --repo "$REPOSITORY"; then
    :
  else
    upload_status=$?
  fi
  retry_post_publish_read_only \
    verify_uploaded_release_asset "$source" "$expected_names" || return 1
  if [ "$upload_status" -ne 0 ]; then
    printf '%s\n' 'asset upload reported failure; exact remote bytes were recovered' >&2
  fi
}

NOTES="$STAGE/RELEASE_NOTES.md"
[ ! -e "$NOTES" ] && [ ! -L "$NOTES" ]
{
  printf '# google-search %s\n\n' "$VERSION"
  printf -- "- Commit: \`%s\`\n" "$COMMIT"
  printf -- "- Signer primary fingerprint: \`%s\`\n" "$SIGNING_FINGERPRINT"
  printf -- "- Publisher: \`longlannet\`\n"
  printf -- "- Main CI run: \`%s\`\n" "$MAIN_RUN_ID"
  printf -- "- Tag CI run: \`%s\`\n\n" "$TAG_RUN_ID"
  printf '## Provenance\n\n'
  printf '%s\n' \
    'GitHub immutable release attestation binds the published release, tag, commit, and asset bytes.' \
    'This is not SLSA build provenance. Source archive provenance also relies on the commit-bound audited archive gate and the signed SHA256SUMS asset.'
} >"$NOTES" || exit 1
command_output_is_exact "$CURRENT_UID:600:1" \
  stat -c '%u:%a:%h' -- "$NOTES"

retry_post_publish_read_only assert_publication_preconditions
refresh_authoritative_keyring
verify_git_commit_signature "$COMMIT"
verify_git_tag_signature "$VERSION"
verify_checksum_signature "$STAGE"
retry_post_publish_read_only assert_release_name_unclaimed
IMMUTABLE_ENABLE_STATUS=0
if gh api --method PUT "repos/$REPOSITORY/immutable-releases" >/dev/null; then
  :
else
  IMMUTABLE_ENABLE_STATUS=$?
fi
retry_post_publish_read_only command_output_is_exact true \
  gh api "repos/$REPOSITORY/immutable-releases" --jq .enabled
if [ "$IMMUTABLE_ENABLE_STATUS" -ne 0 ]; then
  printf '%s\n' 'immutable enable reported failure; exact enabled state was recovered' >&2
fi
retry_post_publish_read_only assert_publication_preconditions
retry_post_publish_read_only assert_release_name_unclaimed
create_draft_with_reconciliation

EXPECTED_ASSET_NAMES=
for name in SHA256SUMS SHA256SUMS.asc "$ASSET"; do
  if [ -z "$EXPECTED_ASSET_NAMES" ]; then
    EXPECTED_ASSET_NAMES="$name"
  else
    EXPECTED_ASSET_NAMES="$EXPECTED_ASSET_NAMES
$name"
  fi
  upload_asset_with_reconciliation "$STAGE/$name" "$EXPECTED_ASSET_NAMES"
done
retry_post_publish_read_only assert_release_asset_names "$EXPECTED_ALL_ASSET_NAMES"
retry_post_publish_read_only assert_release_ids_exact
retry_post_publish_read_only capture_release_asset_manifest
retry_post_publish_read_only assert_release_asset_manifest_exact "$STAGE"
```

Draft 必须绑定 `longlannet` 作者、精确 tag、标题、正文、数值 REST/database ID，并恰好有三个预期资产。发布前下载回独立目录，完成 allowlist、逐字节、hash 和 GPG 验证，再复核 main/tag 与两次精确 commit CI。发布命令若非零，只有同一 Release ID 已经成为 immutable Latest 且其他身份仍全部精确时才可继续：

```bash
download_release_assets_by_id_once() {
  local manifest_before manifest_after
  local name asset_id size digest extra destination attempt_directory finding
  [ -z "$VERIFIED_ASSETS" ] || return 1
  manifest_before="$(observe_release_asset_manifest \
    "$EXPECTED_ALL_ASSET_NAMES" "$STAGE")" || return 1
  [ "$manifest_before" = "$RELEASE_ASSET_MANIFEST" ] || return 1
  attempt_directory="$(mktemp -d "$VERIFY/release-assets.XXXXXXXX")" || return 1
  command_output_is_exact "$CURRENT_UID:700" \
    stat -c '%u:%a' -- "$attempt_directory" || return 1
  while IFS='|' read -r name asset_id size digest extra; do
    [ -z "$extra" ] || return 1
    case "$name" in "$ASSET"|SHA256SUMS|SHA256SUMS.asc) ;; *) return 1 ;; esac
    [[ "$asset_id" =~ ^[1-9][0-9]*$ ]] || return 1
    destination="$attempt_directory/$name"
    [ ! -e "$destination" ] && [ ! -L "$destination" ] || return 1
    gh api --header 'Accept: application/octet-stream' \
      "repos/$REPOSITORY/releases/assets/$asset_id" >"$destination" || return 1
    [ -f "$destination" ] && [ ! -L "$destination" ] || return 1
    command_output_is_exact "$CURRENT_UID:600:1" \
      stat -c '%u:%a:%h' -- "$destination" || return 1
    cmp -- "$STAGE/$name" "$destination" >&2 || return 1
  done <<<"$manifest_before"
  manifest_after="$(observe_release_asset_manifest \
    "$EXPECTED_ALL_ASSET_NAMES" "$STAGE")" || return 1
  [ "$manifest_after" = "$manifest_before" ] || return 1
  finding="$(find -P "$attempt_directory" -mindepth 1 -maxdepth 1 \
    ! -name "$ASSET" ! -name SHA256SUMS ! -name SHA256SUMS.asc \
    -print -quit)" || return 1
  [ -z "$finding" ] || return 1
  VERIFIED_ASSETS="$attempt_directory"
  if ! assert_release_asset_manifest_exact "$VERIFIED_ASSETS"; then
    VERIFIED_ASSETS=
    return 1
  fi
  printf '%s\n' "$VERIFIED_ASSETS"
}
VERIFIED_ASSETS="$(retry_post_publish_read_only \
  download_release_assets_by_id_once)" || exit 1
case "$VERIFIED_ASSETS" in "$VERIFY/release-assets."????????) ;; *) exit 1 ;; esac
command_output_is_exact "$VERIFIED_ASSETS" readlink -m -- "$VERIFIED_ASSETS"
command_output_is_exact "$CURRENT_UID:700" stat -c '%u:%a' -- "$VERIFIED_ASSETS"
(
  cd "$VERIFIED_ASSETS"
  sha256sum --check --strict SHA256SUMS
)
verify_checksum_signature "$VERIFIED_ASSETS"

assert_published_release_state() {
  local state latest_identity
  state="$(gh release view "$VERSION" --repo "$REPOSITORY" \
    --json isDraft,isPrerelease,isImmutable \
    --jq '[.isDraft,.isPrerelease,.isImmutable] | join("|")')" || return 1
  [ "$state" = 'false|false|true' ] || return 1
  latest_identity="$(gh api "repos/$REPOSITORY/releases/latest" \
    --jq '[.id,.tag_name] | map(tostring) | join("|")')" || return 1
  [ "$latest_identity" = "$RELEASE_REST_ID|$VERSION" ] || return 1
  assert_release_ids_exact || return 1
  assert_release_identity || return 1
  assert_release_asset_manifest_exact "$VERIFIED_ASSETS" || return 1
  if [ -n "$RELEASE_PUBLISHED_AT" ]; then
    command_output_is_exact "$RELEASE_PUBLISHED_AT" \
      gh api "repos/$REPOSITORY/releases/tags/$VERSION" --jq .published_at || return 1
  fi
}
capture_release_published_at() {
  local observed
  [ -z "$RELEASE_PUBLISHED_AT" ] || return 1
  observed="$(gh api "repos/$REPOSITORY/releases/tags/$VERSION" --jq .published_at)" || return 1
  [[ "$observed" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]] || return 1
  RELEASE_PUBLISHED_AT="$observed"
}
publish_release_with_reconciliation() {
  local publish_status=0
  if assert_published_release_state; then
    printf '%s\n' 'exact immutable Latest already exists; no publish write was sent' >&2
    return 0
  fi
  assert_exact_draft || return 1
  assert_release_ids_exact || return 1
  assert_release_asset_manifest_exact "$VERIFIED_ASSETS" || return 1
  if gh release edit "$VERSION" --repo "$REPOSITORY" \
    --draft=false --prerelease=false --latest --verify-tag; then
    :
  else
    publish_status=$?
  fi
  retry_post_publish_read_only assert_published_release_state || return 1
  if [ "$publish_status" -ne 0 ]; then
    printf '%s\n' 'publish reported failure; exact immutable Latest was recovered' >&2
  fi
}

retry_post_publish_read_only assert_publication_preconditions
retry_post_publish_read_only command_output_is_exact true \
  gh api "repos/$REPOSITORY/immutable-releases" --jq .enabled
assert_exact_draft
assert_release_ids_exact
assert_release_asset_manifest_exact "$VERIFIED_ASSETS"
publish_release_with_reconciliation
retry_post_publish_read_only capture_release_published_at
retry_post_publish_read_only assert_published_release_state
```

## 发布后终验

Immutable 只在 draft 发布后生效。终验必须同时覆盖认证状态、GitHub release attestation、三个资产、匿名 REST Latest 身份、匿名固定版本下载和三个独立 `latest/download` 路由：

```bash
retry_post_publish_read_only assert_published_release_state
retry_post_publish_read_only assert_publication_preconditions
retry_post_publish_read_only gh release verify "$VERSION" --repo "$REPOSITORY"
for file in "$VERIFIED_ASSETS/$ASSET" "$VERIFIED_ASSETS/SHA256SUMS" \
  "$VERIFIED_ASSETS/SHA256SUMS.asc"; do
  retry_post_publish_read_only \
    gh release verify-asset "$VERSION" "$file" --repo "$REPOSITORY"
done

PUBLIC="$(mktemp -d /tmp/google-search-release-public.XXXXXXXX)" || exit 1
command_output_is_exact "$CURRENT_UID:700" stat -c '%u:%a' -- "$PUBLIC"
download_public_asset_once() {
  local route="$1"
  local name="$2"
  local destination="$3"
  local expected expected_size url
  case "$name" in "$ASSET"|SHA256SUMS|SHA256SUMS.asc) ;; *) return 1 ;; esac
  case "$route" in
    versioned)
      expected="$PUBLIC/$name"
      url="https://github.com/$REPOSITORY/releases/download/$VERSION/$name"
      ;;
    latest)
      expected="$PUBLIC/latest/$name"
      url="https://github.com/$REPOSITORY/releases/latest/download/$name"
      ;;
    *) return 1 ;;
  esac
  [ "$destination" = "$expected" ] || return 1
  expected_size="$(stat -c %s -- "$VERIFIED_ASSETS/$name")" || return 1
  [[ "$expected_size" =~ ^[1-9][0-9]*$ ]] || return 1
  if [ -e "$destination" ] || [ -L "$destination" ]; then
    [ -f "$destination" ] && [ ! -L "$destination" ] || return 1
    command_output_is_exact "$CURRENT_UID:600:1" \
      stat -c '%u:%a:%h' -- "$destination" || return 1
  fi
  env -i PATH=/usr/bin:/bin LC_ALL=C /usr/bin/curl -q --fail --location \
    --proto '=https' --proto-redir '=https' --tlsv1.2 \
    --connect-timeout 10 --max-time 120 --max-filesize "$expected_size" \
    --silent --show-error \
    --output "$destination" "$url" || return 1
  [ -f "$destination" ] && [ ! -L "$destination" ] || return 1
  command_output_is_exact "$CURRENT_UID:600:1" \
    stat -c '%u:%a:%h' -- "$destination" || return 1
  cmp -- "$VERIFIED_ASSETS/$name" "$destination" || return 1
}
for name in "$ASSET" SHA256SUMS SHA256SUMS.asc; do
  retry_post_publish_read_only \
    download_public_asset_once versioned "$name" "$PUBLIC/$name"
done
(
  cd "$PUBLIC"
  sha256sum --check --strict SHA256SUMS
)
verify_checksum_signature "$PUBLIC"

anonymous_latest_release_id() {
  local document_path="$1"
  /usr/bin/python3 -I -S - "$document_path" "$VERSION" "$ASSET" \
    "$VERIFIED_ASSETS" "$RELEASE_REST_ID" "$RELEASE_ASSET_MANIFEST" \
    "$RELEASE_PUBLISHED_AT" <<'PY'
import hashlib
import json
import os
import stat
import sys

(
    document_path,
    version,
    archive_name,
    verified_path,
    expected_id,
    expected_manifest,
    expected_published_at,
) = sys.argv[1:]


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def reject_constant(_value):
    raise ValueError('non-finite JSON number')


try:
    with open(document_path, 'rb') as handle:
        document = json.load(
            handle,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
    raise SystemExit(1)
if not isinstance(document, dict):
    raise SystemExit(1)
expected_names = sorted((archive_name, 'SHA256SUMS', 'SHA256SUMS.asc'))
assets = document.get('assets')
if not isinstance(assets, list) or len(assets) != 3:
    raise SystemExit(1)
release_id = document.get('id')
author = document.get('author')
if (
    isinstance(release_id, bool)
    or not isinstance(release_id, int)
    or release_id <= 0
    or str(release_id) != expected_id
    or document.get('tag_name') != version
    or document.get('name') != f'google-search {version}'
    or document.get('draft') is not False
    or document.get('prerelease') is not False
    or document.get('immutable') is not True
    or document.get('published_at') != expected_published_at
    or not isinstance(author, dict)
    or author.get('login') != 'longlannet'
):
    raise SystemExit(1)

assets_by_name = {}
asset_ids = set()
for asset in assets:
    if not isinstance(asset, dict):
        raise SystemExit(1)
    name = asset.get('name')
    if not isinstance(name, str):
        raise SystemExit(1)
    asset_id = asset.get('id')
    size = asset.get('size')
    if isinstance(asset_id, bool) or not isinstance(asset_id, int) or asset_id <= 0:
        raise SystemExit(1)
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise SystemExit(1)
    if asset.get('state') != 'uploaded':
        raise SystemExit(1)
    if name in assets_by_name or asset_id in asset_ids:
        raise SystemExit(1)
    assets_by_name[name] = asset
    asset_ids.add(asset_id)
if sorted(assets_by_name) != expected_names:
    raise SystemExit(1)
observed_manifest = '\n'.join(
    f"{name}|{asset['id']}|{asset['size']}|{asset['digest']}"
    for name, asset in sorted(assets_by_name.items())
)
if observed_manifest != expected_manifest:
    raise SystemExit(1)

directory_flags = os.O_RDONLY | os.O_DIRECTORY
if hasattr(os, 'O_NOFOLLOW'):
    directory_flags |= os.O_NOFOLLOW
try:
    directory_fd = os.open(verified_path, directory_flags)
except OSError:
    raise SystemExit(1)
try:
    directory_stat = os.fstat(directory_fd)
    if (
        not stat.S_ISDIR(directory_stat.st_mode)
        or directory_stat.st_uid != os.getuid()
        or directory_stat.st_mode & 0o077
    ):
        raise SystemExit(1)
    for name in expected_names:
        file_flags = os.O_RDONLY | os.O_NONBLOCK
        if hasattr(os, 'O_NOFOLLOW'):
            file_flags |= os.O_NOFOLLOW
        try:
            file_fd = os.open(name, file_flags, dir_fd=directory_fd)
        except OSError:
            raise SystemExit(1)
        try:
            file_stat = os.fstat(file_fd)
            if (
                not stat.S_ISREG(file_stat.st_mode)
                or file_stat.st_uid != os.getuid()
                or file_stat.st_nlink != 1
                or file_stat.st_mode & 0o077
            ):
                raise SystemExit(1)
            digest = hashlib.sha256()
            observed_size = 0
            while True:
                chunk = os.read(file_fd, 1024 * 1024)
                if not chunk:
                    break
                observed_size += len(chunk)
                digest.update(chunk)
        finally:
            os.close(file_fd)
        asset = assets_by_name[name]
        if (
            observed_size != file_stat.st_size
            or asset['size'] != observed_size
            or asset.get('digest') != f'sha256:{digest.hexdigest()}'
        ):
            raise SystemExit(1)
finally:
    os.close(directory_fd)
print(release_id)
PY
}
fetch_anonymous_latest_once() {
  local observed_id
  env -i PATH=/usr/bin:/bin LC_ALL=C /usr/bin/curl -q --fail --location \
    --proto '=https' --proto-redir '=https' --tlsv1.2 \
    --connect-timeout 10 --max-time 30 --max-filesize 1048576 \
    --silent --show-error \
    --header 'Accept: application/vnd.github+json' \
    --header 'X-GitHub-Api-Version: 2022-11-28' \
    --output "$PUBLIC/latest.json" \
    "https://api.github.com/repos/$REPOSITORY/releases/latest" || return 1
  [ -f "$PUBLIC/latest.json" ] && [ ! -L "$PUBLIC/latest.json" ] || return 1
  command_output_is_exact "$CURRENT_UID:600:1" \
    stat -c '%u:%a:%h' -- "$PUBLIC/latest.json" || return 1
  observed_id="$(anonymous_latest_release_id "$PUBLIC/latest.json")" || return 1
  [ "$observed_id" = "$RELEASE_REST_ID" ] || return 1
  ANONYMOUS_LATEST_RELEASE_ID="$observed_id"
}
retry_post_publish_read_only fetch_anonymous_latest_once

mkdir -m 700 -- "$PUBLIC/latest"
command_output_is_exact "$CURRENT_UID:700" stat -c '%u:%a' -- "$PUBLIC/latest"
for name in "$ASSET" SHA256SUMS SHA256SUMS.asc; do
  retry_post_publish_read_only \
    download_public_asset_once latest "$name" "$PUBLIC/latest/$name"
done
(
  cd "$PUBLIC/latest"
  sha256sum --check --strict SHA256SUMS
)
verify_checksum_signature "$PUBLIC/latest"
retry_post_publish_read_only assert_published_release_state
retry_post_publish_read_only assert_publication_preconditions
```

全部终验通过后，先计算三个资产摘要并准备尚未完成的 root-owned `0600` evidence 临时文件。随后删除全部发布临时目录、恢复原 active account，并按 `gh` lock、仓库 lock 的顺序解锁；只有这些动作也全部成功后，才向临时文件追加 cleanup 证明和 `complete=true`，fsync 后用不覆盖既有目标的 hard link 原子发布。文件保留 Release REST/database ID、精确 refs、两次 CI run、fingerprint 和三个资产各自 SHA-256：

```bash
prepare_incomplete_release_evidence() {
  local verified_at manifest_count sync_status
  local name asset_id size digest extra
  [ -z "$EVIDENCE_TEMP" ] || return 1
  [ "$EVIDENCE_RECOVERY_READY" -eq 0 ] || return 1
  [ "$EVIDENCE_COMPLETION_APPENDED" -eq 0 ] || return 1
  prepare_release_evidence_target || return 1
  [[ "$COMMIT" =~ ^[0-9a-f]{40}$ ]] || return 1
  [[ "$LOCAL_TAG_OBJECT" =~ ^[0-9a-f]{40}$ ]] || return 1
  [ "$REMOTE_TAG_OBJECT" = "$LOCAL_TAG_OBJECT" ] || return 1
  [ "$REMOTE_TAG_COMMIT" = "$COMMIT" ] || return 1
  [[ "$MAIN_RUN_ID" =~ ^[1-9][0-9]*$ ]] || return 1
  [[ "$TAG_RUN_ID" =~ ^[1-9][0-9]*$ ]] || return 1
  [ "$MAIN_RUN_ID" != "$TAG_RUN_ID" ] || return 1
  [[ "$RELEASE_REST_ID" =~ ^[1-9][0-9]*$ ]] || return 1
  [ "$RELEASE_DATABASE_ID" = "$RELEASE_REST_ID" ] || return 1
  [ "$ANONYMOUS_LATEST_RELEASE_ID" = "$RELEASE_REST_ID" ] || return 1
  [[ "$RELEASE_PUBLISHED_AT" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]] || return 1
  manifest_count="$(printf '%s\n' "$RELEASE_ASSET_MANIFEST" | awk 'NF {count += 1} END {print count + 0}')" || return 1
  [ "$manifest_count" -eq 3 ] || return 1
  ARCHIVE_SHA256="$(sha256_of_file "$VERIFIED_ASSETS/$ASSET")" || return 1
  CHECKSUMS_SHA256="$(sha256_of_file "$VERIFIED_ASSETS/SHA256SUMS")" || return 1
  CHECKSUM_SIGNATURE_SHA256="$(sha256_of_file \
    "$VERIFIED_ASSETS/SHA256SUMS.asc")" || return 1
  verified_at="$(date -u '+%Y-%m-%dT%H:%M:%SZ')" || return 1
  EVIDENCE_TEMP="$(mktemp \
    "$EVIDENCE_DIRECTORY/.google-search-${VERSION}.evidence.XXXXXXXX")" || return 1
  chmod 600 "$EVIDENCE_TEMP" || return 1
  {
    printf 'repository=%s\n' "$REPOSITORY"
    printf 'version=%s\n' "$VERSION"
    printf 'commit=%s\n' "$COMMIT"
    printf 'remote_main=%s\n' "$COMMIT"
    printf 'local_tag_object=%s\n' "$LOCAL_TAG_OBJECT"
    printf 'remote_tag_object=%s\n' "$REMOTE_TAG_OBJECT"
    printf 'remote_tag_commit=%s\n' "$REMOTE_TAG_COMMIT"
    printf 'main_run_id=%s\n' "$MAIN_RUN_ID"
    printf 'tag_run_id=%s\n' "$TAG_RUN_ID"
    printf 'release_rest_id=%s\n' "$RELEASE_REST_ID"
    printf 'release_database_id=%s\n' "$RELEASE_DATABASE_ID"
    printf 'release_published_at=%s\n' "$RELEASE_PUBLISHED_AT"
    printf 'signing_fingerprint=%s\n' "$SIGNING_FINGERPRINT"
    printf 'asset_%s_sha256=%s\n' "$ASSET" "$ARCHIVE_SHA256"
    printf 'asset_SHA256SUMS_sha256=%s\n' "$CHECKSUMS_SHA256"
    printf 'asset_SHA256SUMS.asc_sha256=%s\n' "$CHECKSUM_SIGNATURE_SHA256"
    while IFS='|' read -r name asset_id size digest extra; do
      [ -z "$extra" ] || return 1
      printf 'release_asset_%s_id=%s\n' "$name" "$asset_id"
      printf 'release_asset_%s_size=%s\n' "$name" "$size"
      printf 'release_asset_%s_digest=%s\n' "$name" "$digest"
    done <<<"$RELEASE_ASSET_MANIFEST"
    printf 'anonymous_latest_release_id=%s\n' "$ANONYMOUS_LATEST_RELEASE_ID"
    printf 'attestation_verified=true\n'
    printf 'versioned_public_assets_verified=true\n'
    printf 'anonymous_latest_verified=true\n'
    printf 'public_verification_completed_at=%s\n' "$verified_at"
  } >"$EVIDENCE_TEMP" || return 1
  command_output_is_exact "$CURRENT_UID:600:1" \
    stat -c '%u:%a:%h' -- "$EVIDENCE_TEMP" || return 1
  trap '' HUP INT TERM
  if /usr/bin/sync -f "$EVIDENCE_TEMP"; then
    EVIDENCE_RECOVERY_READY=1
  else
    sync_status=$?
    set_release_signal_traps || return 1
    return "$sync_status"
  fi
  set_release_signal_traps
}
atomic_publish_completed_evidence() {
  /usr/bin/python3 -I -S - "$EVIDENCE_DIRECTORY" "$EVIDENCE_TEMP" \
    "$EVIDENCE_FILE" "$CURRENT_UID" <<'PY'
import os
import stat
import sys

directory_path, temporary_path, final_path, expected_uid_text = sys.argv[1:]
expected_uid = int(expected_uid_text)
if (
    os.path.dirname(temporary_path) != directory_path
    or os.path.dirname(final_path) != directory_path
):
    raise SystemExit(1)
temporary_name = os.path.basename(temporary_path)
final_name = os.path.basename(final_path)
if not temporary_name.startswith('.google-search-v2.0.0.evidence.'):
    raise SystemExit(1)
if final_name != 'google-search-v2.0.0.txt':
    raise SystemExit(1)

directory_flags = os.O_RDONLY | os.O_DIRECTORY
file_flags = os.O_RDONLY | os.O_NONBLOCK
if hasattr(os, 'O_NOFOLLOW'):
    directory_flags |= os.O_NOFOLLOW
    file_flags |= os.O_NOFOLLOW
directory_fd = os.open(directory_path, directory_flags)
linked = False
temporary_removed = False
temporary_stat = None
try:
    directory_stat = os.fstat(directory_fd)
    if (
        not stat.S_ISDIR(directory_stat.st_mode)
        or directory_stat.st_uid != expected_uid
        or stat.S_IMODE(directory_stat.st_mode) != 0o700
    ):
        raise OSError('unsafe evidence directory')
    temporary_fd = os.open(temporary_name, file_flags, dir_fd=directory_fd)
    try:
        temporary_stat = os.fstat(temporary_fd)
        if (
            not stat.S_ISREG(temporary_stat.st_mode)
            or temporary_stat.st_uid != expected_uid
            or stat.S_IMODE(temporary_stat.st_mode) != 0o600
            or temporary_stat.st_nlink != 1
        ):
            raise OSError('unsafe evidence temporary file')
        chunks = []
        total = 0
        while True:
            chunk = os.read(temporary_fd, 65536)
            if not chunk:
                break
            total += len(chunk)
            if total > 1024 * 1024:
                raise OSError('evidence file is unexpectedly large')
            chunks.append(chunk)
        if not b''.join(chunks).endswith(b'\ncomplete=true\n'):
            raise OSError('evidence is not complete')
        os.fsync(temporary_fd)
    finally:
        os.close(temporary_fd)
    try:
        os.stat(final_name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        raise FileExistsError(final_path)
    os.link(
        temporary_name,
        final_name,
        src_dir_fd=directory_fd,
        dst_dir_fd=directory_fd,
        follow_symlinks=False,
    )
    linked = True
    final_stat = os.stat(final_name, dir_fd=directory_fd, follow_symlinks=False)
    if (
        (final_stat.st_dev, final_stat.st_ino)
        != (temporary_stat.st_dev, temporary_stat.st_ino)
        or final_stat.st_nlink != 2
    ):
        raise OSError('evidence hard-link identity mismatch')
    os.fsync(directory_fd)
    os.unlink(temporary_name, dir_fd=directory_fd)
    temporary_removed = True
    final_stat = os.stat(final_name, dir_fd=directory_fd, follow_symlinks=False)
    if (
        (final_stat.st_dev, final_stat.st_ino)
        != (temporary_stat.st_dev, temporary_stat.st_ino)
        or final_stat.st_nlink != 1
    ):
        raise OSError('final evidence identity mismatch')
    os.fsync(directory_fd)
except BaseException:
    if linked:
        try:
            if temporary_removed:
                os.link(
                    final_name,
                    temporary_name,
                    src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd,
                    follow_symlinks=False,
                )
            restored = os.stat(
                temporary_name,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
            final_stat = os.stat(
                final_name,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
            if (
                (restored.st_dev, restored.st_ino)
                == (final_stat.st_dev, final_stat.st_ino)
                == (temporary_stat.st_dev, temporary_stat.st_ino)
            ):
                os.unlink(final_name, dir_fd=directory_fd)
                os.fsync(directory_fd)
        except OSError:
            pass
    raise
finally:
    os.close(directory_fd)
PY
}
finalize_completed_release_evidence() {
  local completed_at
  [ -n "$EVIDENCE_TEMP" ] || return 1
  [ "$EVIDENCE_RECOVERY_READY" -eq 1 ] || return 1
  command_output_is_exact "$EVIDENCE_TEMP" readlink -m -- "$EVIDENCE_TEMP" || return 1
  command_output_is_exact "$CURRENT_UID:600:1" \
    stat -c '%u:%a:%h' -- "$EVIDENCE_TEMP" || return 1
  [ -z "$PUBLIC" ] && [ -z "$VERIFY" ] && [ -z "$VERIFIED_ASSETS" ] && \
    [ -z "$STAGE" ] || return 1
  [ -z "$FRESH_KEYRING" ] && [ -z "$CANARY" ] && [ -z "$AUDITED_DIRECTORY" ] || return 1
  [ -z "$RELEASE_CHECKOUT" ] || return 1
  [ "$GH_ACCOUNT_SWITCHED" -eq 0 ] || return 1
  [ -z "$GH_AUTH_LOCK" ] && [ -z "$PUBLICATION_LOCK" ] || return 1
  [ -z "$GH_AUTH_LOCK_IDENTITY" ] && [ -z "$PUBLICATION_LOCK_IDENTITY" ] || return 1
  trap '' HUP INT TERM
  if [ "$EVIDENCE_COMPLETION_APPENDED" -eq 0 ]; then
    [ ! -e "$EVIDENCE_FILE" ] && [ ! -L "$EVIDENCE_FILE" ] || return 1
    completed_at="$(date -u '+%Y-%m-%dT%H:%M:%SZ')" || return 1
    {
      printf 'temporary_paths_removed=true\n'
      printf 'original_github_account_restored=true\n'
      printf 'publication_locks_released=true\n'
      printf 'completed_at=%s\n' "$completed_at"
      printf 'complete=true\n'
    } >>"$EVIDENCE_TEMP" || return 1
    EVIDENCE_COMPLETION_APPENDED=1
  fi
  command_output_is_exact complete=true tail -n 1 -- "$EVIDENCE_TEMP" || return 1
  command_output_is_exact "$CURRENT_UID:600:1" \
    stat -c '%u:%a:%h' -- "$EVIDENCE_TEMP" || return 1
  atomic_publish_completed_evidence || return 1
  EVIDENCE_TEMP=
  EVIDENCE_RECOVERY_READY=0
  set_release_signal_traps
}
prepare_incomplete_release_evidence

remove_private_release_directory "$PUBLIC"
PUBLIC=
remove_private_release_directory "$VERIFY"
VERIFY=
VERIFIED_ASSETS=
remove_private_release_directory "$STAGE"
STAGE=
stop_private_gpg_agent "$FRESH_KEYRING"
remove_private_release_directory "$FRESH_KEYRING"
FRESH_KEYRING=
remove_private_release_directory "$AUDITED_DIRECTORY"
AUDITED_DIRECTORY=
cd /root
remove_private_release_directory "$RELEASE_CHECKOUT"
RELEASE_CHECKOUT=
timeout --signal=TERM --kill-after=5s 30s \
  gh auth switch --hostname github.com --user "$ORIGINAL_GH_ACCOUNT"
command_output_is_exact "$ORIGINAL_GH_ACCOUNT" \
  timeout --signal=TERM --kill-after=5s 30s gh api user --jq .login
GH_ACCOUNT_SWITCHED=0
trap '' HUP INT TERM
[ "$GH_AUTH_LOCK" = "$GH_AUTH_LOCK_TARGET" ]
command_output_is_exact "$GH_AUTH_LOCK_IDENTITY" stat -c '%d:%i' -- "$GH_AUTH_LOCK"
rmdir -- "$GH_AUTH_LOCK"
GH_AUTH_LOCK=
GH_AUTH_LOCK_IDENTITY=
set_release_signal_traps
trap '' HUP INT TERM
[ "$PUBLICATION_LOCK" = "$PUBLICATION_LOCK_TARGET" ]
command_output_is_exact "$PUBLICATION_LOCK_IDENTITY" \
  stat -c '%d:%i' -- "$PUBLICATION_LOCK"
rmdir -- "$PUBLICATION_LOCK"
PUBLICATION_LOCK=
PUBLICATION_LOCK_IDENTITY=
set_release_signal_traps
finalize_completed_release_evidence
trap - EXIT
trap - HUP INT TERM
```

上述命令已清理并证明 root-owned release checkout、candidate、staging、download、public 与全部 fresh keyring 都不存在，并在恢复原始 GitHub active account 后释放两个本机 lock。版本 evidence 文件保留在 `/root/google-search-release-evidence`，不得在发布清理中删除。如果 immutable 发布已经完成且已 fsync 的 evidence 临时文件尚未原子落为最终文件，EXIT cleanup 会保留并报告这个 root-owned `0600` 恢复文件，同时以失败退出；不得删除它、覆盖正式 evidence 或重跑同版本发布，必须先按已发布的固定 Release ID 与公开资产重新核验后再人工完成恢复。
